import os
import weaviate
from dotenv import load_dotenv

load_dotenv()

client = weaviate.connect_to_local(
    host=os.getenv("WEAVIATE_HOST"),
    port=int(os.getenv("WEAVIATE_HTTP_PORT")),
    grpc_port=int(os.getenv("WEAVIATE_GRPC_PORT")),
)

print(client.is_ready())

client.close()