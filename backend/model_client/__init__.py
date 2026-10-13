"""
MedAgentPro - model client.

LiteLLM downloads its model and price map from GitHub on import unless told not to. Prices, the P1 check
and the tests would then depend on the network and change unseen, and pinning litellm==1.105.0 would pin
nothing. So the local map shipped with the pinned version is used, set here before any import of LiteLLM
(task 5, H1, 12.10).
"""

import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
