import json
from typing import Optional

import networkx as nx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.future import select
from sqlalchemy.orm import sessionmaker

from authtoken_api import get_current_user
from database.db import get_session
from database.models import Artists, MusiqlRepository, RecordArtistAssociation
from .models_api import get_model

model_visualization_router = APIRouter()


async def _enrich_nodes(
    uris: list[str], session_maker: sessionmaker
) -> dict[str, dict]:
    """Return {uri: {title, artist}} for each URI in the list."""
    async with session_maker() as session:
        track_result = await session.execute(
            select(MusiqlRepository.uri, MusiqlRepository.title).where(
                MusiqlRepository.uri.in_(uris)
            )
        )
        titles = {row.uri: row.title for row in track_result}

        artist_result = await session.execute(
            select(RecordArtistAssociation.record_uri, Artists.artist_name)
            .join(Artists, Artists.uri == RecordArtistAssociation.artist_uri)
            .where(RecordArtistAssociation.record_uri.in_(uris))
        )
        artists: dict[str, list[str]] = {}
        for row in artist_result:
            artists.setdefault(row.record_uri, []).append(row.artist_name)

    return {
        uri: {
            "title": titles.get(uri, uri),
            "artist": ", ".join(artists.get(uri, [])) or "Unknown",
        }
        for uri in uris
    }


def _build_subgraph(
    graph: nx.DiGraph,
    seed_uri: Optional[str],
    depth: int,
    top_k: int,
) -> nx.DiGraph:
    """Return a pruned subgraph: ego-graph if seed given, else top-k trimmed full graph."""
    if seed_uri:
        if seed_uri not in graph:
            return nx.DiGraph()
        ego = nx.ego_graph(graph, seed_uri, radius=depth)
    else:
        ego = graph

    # Keep only top_k outgoing edges per node by weight
    pruned = nx.DiGraph()
    pruned.add_nodes_from(ego.nodes())
    for node in ego.nodes():
        out_edges = sorted(
            ego.out_edges(node, data=True),
            key=lambda e: e[2].get("weight", 0),
            reverse=True,
        )[:top_k]
        for u, v, data in out_edges:
            if v in ego:
                pruned.add_edge(u, v, **data)

    return pruned


@model_visualization_router.get("/model/{model_uri}/graph")
async def get_model_graph(
    model_uri: str,
    top_k: int = 10,
    seed_uri: Optional[str] = None,
    depth: int = 2,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user),
):
    try:
        graph: nx.DiGraph = get_model(model_uri)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Model not found"
        )

    subgraph = _build_subgraph(graph, seed_uri, depth, top_k)
    uris = list(subgraph.nodes())
    metadata = await _enrich_nodes(uris, session_maker)

    nodes = [
        {
            "id": uri,
            "title": metadata[uri]["title"],
            "artist": metadata[uri]["artist"],
        }
        for uri in uris
    ]
    edges = [
        {"source": u, "target": v, "weight": round(data.get("weight", 0), 4)}
        for u, v, data in subgraph.edges(data=True)
    ]

    return JSONResponse(
        {
            "nodes": nodes,
            "edges": edges,
            "node_count": len(nodes),
            "edge_count": len(edges),
        }
    )


@model_visualization_router.get(
    "/model/{model_uri}/graph/viz", response_class=HTMLResponse
)
async def get_model_graph_viz(
    model_uri: str,
    top_k: int = 10,
    seed_uri: Optional[str] = None,
    depth: int = 2,
    session_maker: sessionmaker = Depends(get_session),
    user_id: str = Depends(get_current_user),
):
    try:
        graph: nx.DiGraph = get_model(model_uri)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Model not found"
        )

    subgraph = _build_subgraph(graph, seed_uri, depth, top_k)
    uris = list(subgraph.nodes())
    metadata = await _enrich_nodes(uris, session_maker)

    vis_nodes = [
        {
            "id": uri,
            "label": metadata[uri]["title"],
            "title": f"{metadata[uri]['title']}<br>{metadata[uri]['artist']}",
            "group": metadata[uri]["artist"],
            "value": subgraph.in_degree(uri),
        }
        for uri in uris
    ]
    vis_edges = [
        {
            "from": u,
            "to": v,
            "value": round(data.get("weight", 0), 4),
            "title": f"weight: {data.get('weight', 0):.4f}",
            "arrows": "to",
        }
        for u, v, data in subgraph.edges(data=True)
    ]

    seed_label = (
        metadata.get(seed_uri, {}).get("title", seed_uri) if seed_uri else "all"
    )
    title = f"GraphAMP — {model_uri[:8]}… — {seed_label} (top {top_k}, depth {depth})"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="https://unpkg.com/vis-network@9.1.9/dist/vis-network.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/vis-network@9.1.9/dist/dist/vis-network.min.css">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, sans-serif; background: #0f0f12; color: #e0e0e0; height: 100vh; display: flex; flex-direction: column; }}
  header {{ padding: 12px 20px; background: #1a1a24; border-bottom: 1px solid #2a2a3a; display: flex; align-items: center; gap: 16px; }}
  header h1 {{ font-size: 14px; font-weight: 600; color: #a78bfa; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .stats {{ font-size: 12px; color: #888; white-space: nowrap; }}
  #controls {{ display: flex; gap: 10px; margin-left: auto; align-items: center; }}
  #controls label {{ font-size: 12px; color: #aaa; }}
  #controls input, #controls select {{ background: #252535; border: 1px solid #3a3a4a; color: #e0e0e0; padding: 4px 8px; border-radius: 4px; font-size: 12px; width: 80px; }}
  #graph-container {{ flex: 1; position: relative; }}
  #graph {{ width: 100%; height: 100%; }}
  #tooltip {{ position: absolute; background: #1e1e2e; border: 1px solid #3a3a4a; border-radius: 6px; padding: 8px 12px; font-size: 12px; pointer-events: none; display: none; max-width: 200px; z-index: 10; }}
  #tooltip .track-title {{ font-weight: 600; color: #a78bfa; margin-bottom: 2px; }}
  #tooltip .track-artist {{ color: #888; }}
</style>
</head>
<body>
<header>
  <h1>GraphAMP Recommendation Graph</h1>
  <span class="stats">{len(vis_nodes)} nodes · {len(vis_edges)} edges · model {model_uri[:12]}…</span>
  <div id="controls">
    <label>Seed URI: <input id="seedInput" type="text" placeholder="track URI" value="{seed_uri or ""}"></label>
    <label>Top-K: <input id="topkInput" type="number" min="1" max="50" value="{top_k}"></label>
    <label>Depth: <input id="depthInput" type="number" min="1" max="5" value="{depth}"></label>
    <button onclick="reloadGraph()" style="background:#7c3aed;color:#fff;border:none;padding:5px 14px;border-radius:4px;cursor:pointer;font-size:12px;">Reload</button>
  </div>
</header>
<div id="graph-container">
  <div id="graph"></div>
  <div id="tooltip"><div class="track-title" id="tip-title"></div><div class="track-artist" id="tip-artist"></div></div>
</div>
<script>
const rawNodes = {json.dumps(vis_nodes)};
const rawEdges = {json.dumps(vis_edges)};

const nodeMetaMap = {{}};
rawNodes.forEach(n => {{ nodeMetaMap[n.id] = {{ title: n.label, artist: n.group }}; }});

const nodes = new vis.DataSet(rawNodes.map(n => ({{
  id: n.id,
  label: n.label.length > 22 ? n.label.slice(0, 20) + '…' : n.label,
  title: n.title,
  group: n.group,
  value: Math.max(1, n.value),
  font: {{ color: '#e0e0e0', size: 11 }},
}})));

const edges = new vis.DataSet(rawEdges.map(e => ({{
  from: e.from,
  to: e.to,
  value: e.value,
  title: e.title,
  arrows: 'to',
  color: {{ color: '#4a4a6a', highlight: '#a78bfa', opacity: 0.6 }},
}})));

const options = {{
  nodes: {{
    shape: 'dot',
    scaling: {{ min: 8, max: 24, label: {{ enabled: false }} }},
    borderWidth: 1,
    borderWidthSelected: 2,
    color: {{
      border: '#5a3a8a',
      background: '#2d1b69',
      highlight: {{ border: '#a78bfa', background: '#4c1d95' }},
      hover: {{ border: '#7c3aed', background: '#3b0764' }},
    }},
  }},
  edges: {{
    smooth: {{ type: 'continuous', roundness: 0.3 }},
    scaling: {{ min: 1, max: 4 }},
  }},
  physics: {{
    stabilization: {{ iterations: 150 }},
    barnesHut: {{ gravitationalConstant: -3000, springLength: 120, damping: 0.15 }},
  }},
  interaction: {{ hover: true, tooltipDelay: 150 }},
}};

const container = document.getElementById('graph');
const network = new vis.Network(container, {{ nodes, edges }}, options);

const tooltip = document.getElementById('tooltip');
network.on('hoverNode', (params) => {{
  const meta = nodeMetaMap[params.node];
  if (!meta) return;
  document.getElementById('tip-title').textContent = meta.title;
  document.getElementById('tip-artist').textContent = meta.artist;
  tooltip.style.display = 'block';
  tooltip.style.left = (params.event.center.x + 12) + 'px';
  tooltip.style.top = (params.event.center.y - 8) + 'px';
}});
network.on('blurNode', () => {{ tooltip.style.display = 'none'; }});
network.on('doubleClick', (params) => {{
  if (params.nodes.length) {{
    const uri = params.nodes[0];
    document.getElementById('seedInput').value = uri;
    reloadGraph();
  }}
}});

function reloadGraph() {{
  const seed = document.getElementById('seedInput').value.trim();
  const topk = document.getElementById('topkInput').value;
  const depth = document.getElementById('depthInput').value;
  const params = new URLSearchParams({{ top_k: topk, depth }});
  if (seed) params.set('seed_uri', seed);
  window.location.href = window.location.pathname + '?' + params.toString();
}}
</script>
</body>
</html>"""

    return HTMLResponse(content=html)
