import sys
import json
import asyncio
import os

# Ensure project root is importable when this file is launched as a stdio server.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.env import load_project_env
load_project_env()

# Save original stdout for JSON-RPC communication
stdout_channel = sys.stdout

# Redirect all standard prints and library outputs to stderr to avoid corrupting JSON-RPC stdout
sys.stdout = sys.stderr

from src.tools.graph_rag_retriever import GraphRAGRetriever
from src.tools.food_search import search_recipes_online
from src.tools.location_map import LocationMap

# Initialize tool clients
retriever = GraphRAGRetriever("obsidian_vault")
map_client = LocationMap()

async def handle_request(req):
    method = req.get("method")
    params = req.get("params", {})
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "superfoodie-mcp-server",
                    "version": "1.0.0"
                }
            }
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "query_diet_safety",
                        "description": "Evaluate food safety based on a user's disease and target food using GraphRAG.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "disease": {
                                    "type": "string",
                                    "description": "The current health condition or disease of the user (e.g., gout, cold)."
                                },
                                "food": {
                                    "type": "string",
                                    "description": "The food item to check for safety (e.g., beef, beer, seafood)."
                                }
                            },
                            "required": ["disease", "food"]
                        }
                    },
                    {
                        "name": "search_recipes_online",
                        "description": "Search for recipe details online, including ingredients, steps, and heat control info.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "The recipe or ingredient query (e.g., tomato egg)."
                                }
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "get_route_duration",
                        "description": "Get transit routing duration estimation between origin and destination using Gaode Map.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "origin": {
                                    "type": "string",
                                    "description": "Starting address or business district name."
                                },
                                "destination": {
                                    "type": "string",
                                    "description": "Ending restaurant name."
                                }
                            },
                            "required": ["origin", "destination"]
                        }
                    }
                ]
            }
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "query_diet_safety":
            disease = arguments.get("disease", "")
            food = arguments.get("food", "")
            res = retriever.query_diet_safety(disease, food)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res, ensure_ascii=False)
                        }
                    ]
                }
            }

        elif tool_name == "search_recipes_online":
            query = arguments.get("query", "")
            res = await search_recipes_online(query)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res, ensure_ascii=False)
                        }
                    ]
                }
            }

        elif tool_name == "get_route_duration":
            origin = arguments.get("origin", "")
            destination = arguments.get("destination", "")
            res = await map_client.get_route_duration(origin, destination, mode="transit")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res, ensure_ascii=False)
                        }
                    ]
                }
            }

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {tool_name}"
                }
            }

    else:
        # Ignore notifications or return empty for unhandled requests
        if req_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
        return None

async def main():
    # A plain stdin loop is more reliable on Windows stdio pipes than connect_read_pipe.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = await handle_request(req)
            if resp:
                stdout_channel.write(json.dumps(resp, ensure_ascii=False) + "\n")
                stdout_channel.flush()
        except Exception as e:
            try:
                err_resp = {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32603,
                        "message": str(e)
                    }
                }
                stdout_channel.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
                stdout_channel.flush()
            except:
                pass

if __name__ == "__main__":
    asyncio.run(main())
