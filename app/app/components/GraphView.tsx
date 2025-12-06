"use client";

import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';

// Dynamically import ForceGraph2D to avoid SSR issues
const ForceGraph2D = dynamic(() => import('react-force-graph-2d'), { ssr: false });

interface GraphData {
    nodes: { id: string; group: number; label: string }[];
    links: { source: string; target: string }[];
}

const GraphView = () => {
    const [graphData, setGraphData] = useState<GraphData>({ nodes: [], links: [] });

    useEffect(() => {
        // Mock data for visualization if backend is not connected
        // In a real app, fetch from Neo4j via an API route
        setGraphData({
            nodes: [
                { id: 'doc_1', group: 1, label: 'Document 1' },
                { id: 'GraphRAG', group: 2, label: 'GraphRAG' },
                { id: 'Neo4j', group: 2, label: 'Neo4j' }
            ],
            links: [
                { source: 'doc_1', target: 'GraphRAG' },
                { source: 'doc_1', target: 'Neo4j' }
            ]
        });
    }, []);

    return (
        <div className="border rounded-lg overflow-hidden h-[600px] bg-slate-900">
            <ForceGraph2D
                graphData={graphData}
                nodeLabel="label"
                nodeAutoColorBy="group"
                linkDirectionalParticles={2}
            />
        </div>
    );
};

export default GraphView;
