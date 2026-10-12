"""Shared test setup. LiteLLM uses the map shipped with the pinned version, never one downloaded on
import (task 5, H1), whichever module a test imports first."""

import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
