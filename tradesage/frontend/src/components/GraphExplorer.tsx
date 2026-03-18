import React, { useEffect, useRef, useState } from 'react'
import * as d3 from 'd3'
import axios from 'axios'
import { Network } from 'lucide-react'

interface GraphNode {
  id: string
  label: string
  name?: string
  outcome?: string
  sentiment?: number
  win_rate?: number
  x?: number
  y?: number
  fx?: number | null
  fy?: number | null
}

interface GraphLink {
  source: string | GraphNode
  target: string | GraphNode
  type: string
}

interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
}

const LABEL_COLORS: Record<string, string> = {
  Company:       '#60a5fa',
  Sector:        '#a855f7',
  NewsEvent:     '#22c55e',
  Trade:         '#eab308',
  Lesson:        '#06b6d4',
  MarketPattern: '#f97316',
  TraderPrinciple: '#ec4899',
}

interface GraphExplorerProps {
  ticker?: string
}

export default function GraphExplorer({ ticker = 'AAPL' }: GraphExplorerProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], links: [] })
  const [loading, setLoading] = useState(false)
  const [selectedTicker, setSelectedTicker] = useState(ticker)
  const [error, setError] = useState('')

  const fetchGraph = async (t: string) => {
    setLoading(true)
    setError('')
    try {
      const resp = await axios.get(`/api/graph/subgraph/${t}`)
      setGraphData(resp.data)
    } catch (e) {
      setError('Could not load graph data')
      setGraphData({ nodes: [], links: [] })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchGraph(selectedTicker)
  }, [selectedTicker])

  useEffect(() => {
    if (!svgRef.current || !graphData.nodes.length) return

    const svg = d3.select(svgRef.current)
    svg.selectAll('*').remove()

    const width = svgRef.current.clientWidth || 600
    const height = 400

    const g = svg.append('g')

    // Zoom
    svg.call(
      d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.3, 3])
        .on('zoom', (event) => g.attr('transform', event.transform))
    )

    // Force simulation
    const simulation = d3.forceSimulation(graphData.nodes as d3.SimulationNodeDatum[])
      .force('link', d3.forceLink(graphData.links).id((d: unknown) => (d as GraphNode).id).distance(80))
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide(30))

    // Links
    const link = g.append('g')
      .selectAll('line')
      .data(graphData.links)
      .join('line')
      .attr('stroke', '#252d40')
      .attr('stroke-width', 1.5)
      .attr('stroke-opacity', 0.7)

    // Link labels
    const linkLabel = g.append('g')
      .selectAll('text')
      .data(graphData.links)
      .join('text')
      .text(d => d.type)
      .attr('font-size', '8px')
      .attr('fill', '#6b7280')
      .attr('text-anchor', 'middle')

    // Nodes
    const node = (g.append('g')
      .selectAll('circle')
      .data(graphData.nodes)
      .join('circle') as d3.Selection<SVGCircleElement, GraphNode, SVGGElement, unknown>)
      .attr('r', d => d.label === 'Company' ? 16 : 10)
      .attr('fill', d => LABEL_COLORS[d.label] || '#94a3b8')
      .attr('fill-opacity', 0.85)
      .attr('stroke', '#0f1117')
      .attr('stroke-width', 2)
      .style('cursor', 'pointer')
      .call(
        d3.drag<SVGCircleElement, GraphNode>()
          .on('start', (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart()
            d.fx = d.x
            d.fy = d.y
          })
          .on('drag', (event, d) => {
            d.fx = event.x
            d.fy = event.y
          })
          .on('end', (event, d) => {
            if (!event.active) simulation.alphaTarget(0)
            d.fx = null
            d.fy = null
          })
      )

    // Node labels
    const nodeLabel = g.append('g')
      .selectAll('text')
      .data(graphData.nodes)
      .join('text')
      .text(d => (d.name || d.id || '').slice(0, 12))
      .attr('font-size', '9px')
      .attr('fill', '#d1d5db')
      .attr('text-anchor', 'middle')
      .attr('dy', '2.2em')
      .style('pointer-events', 'none')

    simulation.on('tick', () => {
      link
        .attr('x1', d => (d.source as GraphNode).x || 0)
        .attr('y1', d => (d.source as GraphNode).y || 0)
        .attr('x2', d => (d.target as GraphNode).x || 0)
        .attr('y2', d => (d.target as GraphNode).y || 0)

      linkLabel
        .attr('x', d => (((d.source as GraphNode).x || 0) + ((d.target as GraphNode).x || 0)) / 2)
        .attr('y', d => (((d.source as GraphNode).y || 0) + ((d.target as GraphNode).y || 0)) / 2)

      node
        .attr('cx', (d: GraphNode) => d.x || 0)
        .attr('cy', (d: GraphNode) => d.y || 0)

      nodeLabel
        .attr('x', (d: GraphNode) => d.x || 0)
        .attr('y', (d: GraphNode) => d.y || 0)
    })

    return () => { simulation.stop() }
  }, [graphData])

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Network className="w-4 h-4 text-brand-glow" />
          <h3 className="text-sm font-semibold text-gray-300">Knowledge Graph Explorer</h3>
        </div>
        <div className="flex items-center gap-2">
          <input
            className="bg-surface-2 border border-surface-3 rounded-lg px-2 py-1 text-sm text-white mono focus:outline-none focus:border-brand uppercase"
            value={selectedTicker}
            onChange={e => setSelectedTicker(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === 'Enter' && fetchGraph(selectedTicker)}
            placeholder="AAPL"
            style={{ width: '80px' }}
          />
          <button className="btn-primary btn text-xs py-1" onClick={() => fetchGraph(selectedTicker)}>
            Explore
          </button>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-2 text-xs">
        {Object.entries(LABEL_COLORS).map(([label, color]) => (
          <div key={label} className="flex items-center gap-1">
            <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: color }} />
            <span className="text-gray-400">{label}</span>
          </div>
        ))}
      </div>

      {loading && (
        <div className="text-center py-4 text-sm text-gray-400">Loading graph...</div>
      )}
      {error && (
        <div className="text-center py-4 text-sm text-accent-red">{error}</div>
      )}
      {!loading && !error && graphData.nodes.length === 0 && (
        <div className="text-center py-4 text-sm text-gray-400">
          No graph data for {selectedTicker}. Run some trades first or check Neo4j.
        </div>
      )}

      <svg
        ref={svgRef}
        className="w-full rounded-lg bg-surface-2"
        style={{ height: '400px', display: graphData.nodes.length ? 'block' : 'none' }}
      />
    </div>
  )
}
