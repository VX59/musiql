import random
import networkx as nx
import pickle
from typing import Optional, List
from functools import lru_cache
from boto3_tools import S3, get_S3


class GraphAMP:
    def __init__(self, model_uri):
        self.model: nx.DiGraph = get_model(model_uri)

    def sample(self, uri: Optional[str]):
        if uri is None:
            return random.choice(list(self.model.nodes))

        out_edges = list(self.model.out_edges(uri, data=True))
        if out_edges:
            targets = [v for _, v, _ in out_edges]
            weights = [data.get("weight", 1e-4) for _, _, data in out_edges]
            next_model_state = random.choices(targets, weights=weights, k=1)[0]
        else:
            next_model_state = random.choice(list(self.model.nodes))

        return next_model_state

    def preempt(self, uri: Optional[str], n: int = 20) -> List[str]:
        samples = []
        for _ in range(n):
            uri = self.sample(uri)
            samples.append(uri)
        return samples


@lru_cache
def get_model(model_uri):
    s3_service: S3 = get_S3()
    obj_key = f"recommendation_models/GAMP/{model_uri}.gamp"
    file_stream = s3_service.pull_obj_stream(obj_key)
    graph_data = file_stream.read()

    return pickle.loads(graph_data)


@lru_cache
def get_recommendation_api(model_uri) -> GraphAMP:
    return GraphAMP(model_uri)
