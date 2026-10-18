"""postgate - a safety gate for bots that post.

Library use::

    from postgate import Gate, Config, Code

    gate = Gate(Config(state_path="state.json", verifier="local"))
    r = gate.before(text)
    if r.code == Code.WAIT: ...           # sleep r.wait_seconds and re-check
    elif r.ok: post_id = my_bot.post(text); gate.after(post_id, text)
    else: stop()
"""
__version__ = "0.1.0"

from .config import Config, ConfigError, load_config  # noqa: E402
from .gate import Code, Gate, Result  # noqa: E402
from .queue import Queue, parse_plan  # noqa: E402
from .text import normalize, similar, text_key  # noqa: E402
from .verifiers import (LocalVerifier, PostInfo, TrustVerifier, Verifier,  # noqa: E402
                        VerifierUnavailable, load_verifier)

__all__ = ["Code", "Config", "ConfigError", "Gate", "LocalVerifier", "PostInfo", "Queue", "Result",
           "TrustVerifier", "Verifier", "VerifierUnavailable", "load_config", "load_verifier",
           "normalize", "parse_plan", "similar", "text_key", "__version__"]
