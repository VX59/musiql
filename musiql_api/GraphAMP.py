import random
import networkx as nx
import pickle
from typing import Optional
from s3_service import S3Service

recommendation_api = None
model = None

class GraphAMP:
    def __init__(self):
       self.model:nx.DiGraph = GraphAMP.get_model()


    def sample(self, uri:Optional[str]):
        if uri is None:
            next_model_state = random.choice(list(self.model.nodes))

        else:
            out_edges = list(self.model.out_edges(uri, data=True))
            if out_edges:
                targets = [v for _, v, _ in out_edges]
                weights = [data.get("weight", 1e-4) for _, _, data in out_edges]
                next_model_state = random.choices(targets, weights=weights, k=1)[0]
                print("next model state ..", next_model_state)
            else:
                next_model_state = random.choice(list(self.model.nodes))

        return next_model_state
    

    @staticmethod
    def get_model():
        global model
        if model is None:
            s3_service = S3Service.get_s3_service()
            obj_key = "recommendation_models/GraphAMP.model"
            file_stream = s3_service.pull_obj_stream(obj_key)
            graph_data = file_stream.read()

            model = pickle.loads(graph_data)
        return model
            

    @classmethod
    def get_recommendation_api(cls):
        global recommendation_api
        if recommendation_api is None:
            recommendation_api = cls()
        return recommendation_api