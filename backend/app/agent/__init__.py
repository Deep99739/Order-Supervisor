"""Model-facing code: the prompt, the answer's shape, and the provider adapters.

Nothing here decides anything. It turns a run's recorded state into a question and a
provider's answer into a validated proposal; authority stays with the workflow.
"""
