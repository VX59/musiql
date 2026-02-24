import random
import networkx as nx
from joblib import load
from pathlib import Path

class GraphAMP:
    def __init__(self):
        model_path = Path("recommendation-models") / "GraphAMP" / "models" / "GraphAMP.graph"
        self.model:nx.DiGraph = load(model_path)
        self.model_state:str = None

    async def sample(self):
        if not self.model_state:
            next_model_state = random.choice(list(self.model.nodes))

        else:

            out_edges = list(self.model.out_edges(self.model_state, data=True))
            if out_edges:
                targets = [v for _, v, _ in out_edges]
                weights = [data["weight"] for _, _, data in out_edges]
                next_model_state = random.choices(targets, weights=weights, k=1)[0]
            else:
                next_model_state = random.choice(list(self.model.nodes))

        self.model_state = next_model_state
        print("next song uri is:", self.model_state)