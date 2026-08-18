"""Nova on GKE — how a customer question flows through the system.

A RUNTIME diagram. Provision- and build-time components are absent on purpose: Terraform,
Cloud Build, Artifact Registry, the seed Job, the eval runner. They create the system; they
take no part in an interaction.

Numbered edges follow one question end to end. Dotted edges are standing background.

STRUCTURE is containment: GCP project -> VPC -> GKE data plane -> namespace. The control
plane sits outside the VPC because it genuinely runs in a Google-owned VPC peered to yours.

COLOUR encodes ownership, not nesting depth:
    grey = outside your control   blue = Google-managed
    green = your GKE cluster      white = your workloads

ICON = what the thing is (Python service, FastAPI app, Postgres, Redis).
LABEL = how it is deployed (Deployment, StatefulSet).

LABELS ARE TWO SHORT LINES, MAX. graphviz draws a fixed-size icon with the label beneath at
full text width, so long labels overrun their neighbours and the page turns to soup. Every
number, CIDR and rationale belongs in docs/architecture-gke.md, not on the canvas.

Render:  python3 docs/diagrams/nova-gke.py   ->  docs/diagrams/nova-gke.png
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.gcp.compute import GKE
from diagrams.gcp.network import NAT
from diagrams.gcp.security import Iam, KMS
from diagrams.k8s.podconfig import Secret
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.onprem.database import PostgreSQL
from diagrams.onprem.inmemory import Redis
from diagrams.onprem.monitoring import Grafana, Prometheus
from diagrams.programming.framework import Fastapi
from diagrams.programming.language import Python

graph_attr = {
    "fontsize": "16",
    "bgcolor": "white",
    "pad": "0.6",
    "nodesep": "1.1",
    "ranksep": "1.7",
    "splines": "spline",
}
node_attr = {"fontsize": "13"}
edge_attr = {"fontsize": "12"}

OUTSIDE = {"bgcolor": "#F3F3F1", "pencolor": "#9AA0A6", "fontsize": "15"}
GOOGLE = {"bgcolor": "#E8F0FE", "pencolor": "#1A73E8", "fontsize": "16"}
GOOGLE_MANAGED = {"bgcolor": "#D2E3FC", "pencolor": "#174EA6", "style": "rounded,dashed", "fontsize": "14"}
NETWORK = {"bgcolor": "#DCE9FB", "pencolor": "#1967D2", "fontsize": "14"}
CLUSTER = {"bgcolor": "#E6F4EA", "pencolor": "#137333", "penwidth": "2", "fontsize": "16"}
NAMESPACE = {"bgcolor": "#FFFFFF", "pencolor": "#5F6368", "style": "rounded,dashed", "fontsize": "14"}

with Diagram(
    "Nova on GKE",
    filename="docs/diagrams/nova-gke",
    show=False,
    direction="TB",
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    with Cluster("INTERNET", graph_attr=OUTSIDE):
        client = Client("customer")
        anthropic = Server("Anthropic API\nclaude-haiku-4-5")
        langfuse = Server("Langfuse\ntraces")

    with Cluster("GCP project · us-west1", graph_attr=GOOGLE):
        with Cluster("GKE CONTROL PLANE — Google-owned, peered", graph_attr=GOOGLE_MANAGED):
            control_plane = GKE("mlops-lifecycle\npublic · 1 /32")

        nat = NAT("Cloud NAT\negress only")
        sm = KMS("Secret Manager")
        gsa = Iam("GSA\nper-secret access")

        with Cluster("VPC — no Ingress, no external IP on any node", graph_attr=NETWORK):
            with Cluster("GKE DATA PLANE — 2 x e2-standard-4 · no GPU", graph_attr=CLUSTER):
                with Cluster("namespace nova", graph_attr=NAMESPACE):
                    nova = Fastapi("nova · Deployment\nFastAPI agent")

                    with Cluster("MCP connectors — 9 tools", graph_attr=NAMESPACE):
                        mcp = [
                            Python("mcp-accounts\n4 tools"),
                            Python("mcp-transactions\n3 tools"),
                            Python("mcp-products\n2 tools"),
                        ]

                    postgres = PostgreSQL("postgres-0 · StatefulSet\nbanking data")
                    redis = Redis("redis-0 · StatefulSet\npersistent memory")

                with Cluster("ns monitoring", graph_attr=NAMESPACE):
                    prometheus = Prometheus("Prometheus\n6 alerts")
                    grafana = Grafana("Grafana")

                eso = Secret("external-secrets\noperator")

    # ---- the interaction, in order -----------------------------------------
    client >> Edge(label="1 · POST /chat") >> control_plane
    control_plane >> Edge(label="2 · port-forward :8000") >> nova
    nova >> Edge(label="3 · load ctx\n8 · save ctx") >> redis
    nova >> Edge(label="4 · prompt + tools\n7 · tool result") >> nat
    nat >> Edge(label="HTTPS :443") >> anthropic
    nova >> Edge(label="5 · MCP :8080") >> mcp
    mcp >> Edge(label="6 · SQL :5432") >> postgres
    nova >> Edge(label="9 · trace", style="dashed") >> nat
    nat >> Edge(style="dashed") >> langfuse

    # ---- observability: Prometheus pulls, Nova pushes nothing ---------------
    prometheus >> Edge(label="scrape /metrics · 30s") >> nova
    grafana >> Edge(label="PromQL") >> prometheus

    # ---- standing background, not part of the request ----------------------
    eso >> Edge(label="Workload Identity", style="dotted") >> gsa
    gsa >> Edge(style="dotted") >> sm
    eso >> Edge(label="syncs Secrets · 1h", style="dotted") >> nova
