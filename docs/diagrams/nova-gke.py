"""Nova on GKE — how a customer question flows through the system.

A RUNTIME diagram. Build- and provision-time components are deliberately absent:
Terraform, Cloud Build, Artifact Registry, the one-shot seed Job, the eval runner.
Those create the system; they take no part in an interaction.

Numbered edges follow one question end to end. Dotted edges are standing background —
credentials the pods already hold, refreshed hourly, not fetched per request.

Trust zones, not public/private subnet lanes: GCP subnets carry no public/private flag,
privacy is `enable_private_nodes = true` (no external IP), Cloud NAT is a regional service
that lives in no subnet, and subnets are regional so there are no AZ columns.
See docs/architecture-gke.md section 2.

LABELS ARE DELIBERATELY SHORT. graphviz gives every node a fixed-size icon and draws the
label beneath it at full width, so long multi-line labels overrun their neighbours. Detail
belongs in the doc, not on the canvas.

Render:  python3 docs/diagrams/nova-gke.py   ->  docs/diagrams/nova-gke.png
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.gcp.compute import GKE
from diagrams.gcp.network import NAT
from diagrams.gcp.security import Iam, KMS
from diagrams.k8s.compute import Deployment, StatefulSet
from diagrams.k8s.ecosystem import Helm
from diagrams.k8s.podconfig import Secret
from diagrams.onprem.client import Client
from diagrams.onprem.compute import Server
from diagrams.programming.framework import Fastapi

# nodesep/ranksep are the overlap fix: wide labels need room on both axes.
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

with Diagram(
    "Nova on GKE — one customer question, end to end",
    filename="docs/diagrams/nova-gke",
    show=False,
    direction="TB",
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    with Cluster("INTERNET — untrusted"):
        client = Client("customer")
        anthropic = Server("Anthropic API\nclaude-haiku-4-5")
        langfuse = Server("Langfuse Cloud\ntraces")

    # The zone note lives in the cluster label, not in a node — a node here overlapped.
    with Cluster("PUBLIC EDGE — no Ingress, no LoadBalancer, no external IP on any node"):
        control_plane = GKE("GKE control plane\npublic endpoint · 1 /32")
        nat = NAT("Cloud NAT\negress only")

    with Cluster(
        "YOUR VPC — us-west1, REGIONAL subnet 10.10.0.0/20 · pods 10.20.0.0/16\n"
        "private because enable_private_nodes = true, not because of a subnet flag"
    ):
        with Cluster("node pool primary — 2 x e2-standard-4 · NO GPU"):
            with Cluster("ns external-secrets"):
                eso = Helm("External Secrets\nOperator")

            with Cluster("namespace nova"):
                nova = Fastapi("nova · svc:8000\nFastAPI + LangChain")

                with Cluster("MCP connectors — 9 tools"):
                    mcp = [
                        Deployment("mcp-accounts\n4 tools"),
                        Deployment("mcp-transactions\n3 tools"),
                        Deployment("mcp-products\n2 tools"),
                    ]

                postgres = StatefulSet("postgres-0\nsvc:5432 · PVC 10Gi")
                redis = StatefulSet("redis-0 redis-stack\nsvc:6379 · PVC 2Gi")
                secrets = Secret("K8s Secrets\nx3")

    with Cluster("GOOGLE-MANAGED — via Private Google Access, never the NAT"):
        sm = KMS("Secret Manager\n4 secrets")
        gsa = Iam("GSA external-secrets\nper-secret access")

    # ---- the interaction, in order -----------------------------------------
    client >> Edge(label="1 · POST /chat") >> control_plane
    control_plane >> Edge(label="2 · tunnel to :8000") >> nova
    nova >> Edge(label="3 · load ctx\n8 · save ctx") >> redis
    nova >> Edge(label="4 · prompt + tools\n7 · tool result") >> nat
    nat >> Edge(label="HTTPS :443") >> anthropic
    nova >> Edge(label="5 · MCP :8080") >> mcp
    mcp >> Edge(label="6 · SQL :5432") >> postgres
    nova >> Edge(label="9 · trace", style="dashed") >> nat
    nat >> Edge(style="dashed") >> langfuse

    # ---- standing background, not part of the request ----------------------
    eso >> Edge(label="WI token", style="dotted") >> gsa
    gsa >> Edge(label="read", style="dotted") >> sm
    eso >> Edge(label="sync 1h", style="dotted") >> secrets
    secrets >> Edge(label="at pod start", style="dotted") >> nova
    secrets >> Edge(style="dotted") >> postgres
