"""Guided model-to-patient tour (WS4/WS6 demo).

    python -m app.tour                 # http://localhost:8600

A full-screen, six-chapter walkthrough (knowledge graph -> GNN -> patient -> integration -> investigation ->
insights) followed by a patient workspace. Every number, edge and observation shown comes from a file in this
repository, the public Alexander Young release, or a live call to the repo's retriever and verifier.
The CPTAC kidney-cancer models are shown as evaluated on CPTAC ccRCC only; they are never applied to the
colorectal case.
"""
