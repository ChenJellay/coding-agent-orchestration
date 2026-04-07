"""
Verification loop and local judge integration.

Responsibilities:
- Checkpoint persistence/rollback around edit attempts
- Static checks hook (demo: placeholder)
- In-process judge integration via plug-and-play chain runtime
- (Legacy) local judge + intent-compiler FastAPI services remain present for compatibility
- LangGraph verification state machine coordinating the above
"""

