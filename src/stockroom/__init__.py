"""Stockroom: a reference inventory/order-support agent with an evaluation harness.

The package is organised as:

* ``stockroom.config``       – all runtime settings, read from environment variables.
* ``stockroom.agent``        – tools, model adapters (live Bedrock + fake), harness, MCP client.
* ``stockroom.evals``        – deterministic metrics, LLM judges, calibration, OTEL tracing.
* ``stockroom.mock_server``  – the MCP inventory server used as the tool boundary.
"""

__version__ = "0.1.0"
