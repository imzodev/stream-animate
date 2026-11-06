"""Voice control: wake word + commands, dictation editing, AI fallback."""

from .catalog import COMMANDS, WAKE_WORD_PRESETS, CatalogCommand
from .config import ActionError, VoiceCommandsConfig, validate_action
from .service import VoiceCommandService

__all__ = [
    "ActionError",
    "COMMANDS",
    "CatalogCommand",
    "WAKE_WORD_PRESETS",
    "VoiceCommandsConfig",
    "VoiceCommandService",
    "validate_action",
    "build_service",
]


def build_service(config, *, engine_getter, llm_config=None, on_feedback=None):
    """Wire the real Windows backends into a :class:`VoiceCommandService`."""

    from .actions import ActionRunner
    from .ai_agent import AIAgent

    windows = None
    tabs = None
    try:
        from .windows import Win32Windows

        windows = Win32Windows()
    except Exception:  # noqa: BLE001 - non-Windows: window actions fail cleanly
        pass

    def tabs_factory():
        from .tabs import UiaTabs

        return UiaTabs()

    def typer():
        engine = engine_getter()
        return getattr(engine, "typer", None)

    class _LazyTyper:
        def type_text(self, text, *, append_space=False):
            t = typer()
            if t is None:
                raise RuntimeError("STT typer is not available")
            return t.type_text(text, append_space=append_space)

    runner = ActionRunner(
        windows=windows,
        tabs_factory=tabs_factory,
        typer=_LazyTyper(),
        engine_getter=engine_getter,
    )

    agent = None
    if config.ai_fallback and llm_config is not None and llm_config.api_key():
        from ..llm.client import FactCheckerClient

        def complete(system: str, user: str) -> str:
            with FactCheckerClient(llm_config) as client:
                return client.complete(system, user, max_tokens=800)

        try:
            tabs = tabs_factory()
        except Exception:  # noqa: BLE001 - tab context is optional
            tabs = None
        agent = AIAgent(complete, windows=windows, tabs=tabs)

    return VoiceCommandService(
        config,
        runner=runner,
        engine_getter=engine_getter,
        agent=agent,
        on_feedback=on_feedback,
    )
