import random
import networkx as nx
from joblib import load
from pathlib import Path
from typing import Optional

recommendation_model = None

class GraphAMP:
    def __init__(self):
        model_path = Path("recommendation-models") / "GraphAMP" / "models" / "GraphAMP.graph"
        self.model:nx.DiGraph = load(model_path)

    def sample(self, uri:Optional[str]):
        if uri is None:
            next_model_state = random.choice(list(self.model.nodes))

        else:
            out_edges = list(self.model.out_edges(uri, data=True))
            if out_edges:
                targets = [v for _, v, _ in out_edges]
                weights = [data.get("weight", 1e-4) for _, _, data in out_edges]
                next_model_state = random.choices(targets, weights=weights, k=1)[0]
            else:
                next_model_state = random.choice(list(self.model.nodes))

        return next_model_state
    
    @classmethod
    def get_model(cls):
        global recommendation_model
        if recommendation_model is None:
            recommendation_model = cls()
        return recommendation_model