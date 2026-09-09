"""Unit tests for the /v1/agents/run endpoint."""

# pylint: disable=protected-access,import-outside-toplevel,unused-argument

from __future__ import annotations

from typing import Any

import pytest
from pytest_mock import MockerFixture

from app.endpoints.agents import run_agent_handler
from models.api.requests.agents import AgentRunRequest


@pytest.fixture(name="mock_config")
def mock_config_fixture(mocker: MockerFixture) -> Any:
    """Mock the configuration singleton."""
    mock_cfg = mocker.patch("app.endpoints.agents.configuration")
    mock_cfg.spawner_configuration = None
    mock_cfg.inference.default_provider = None
    mock_cfg.inference.default_model = None
    return mock_cfg


@pytest.fixture(name="mock_executor")
def mock_executor_fixture(mocker: MockerFixture) -> Any:
    """Mock the step executor via cloud-agents dispatch."""
    mock_result = mocker.MagicMock()
    mock_result.status = "completed"
    mock_result.output = {"summary": "Done"}
    mock_result.error = None
    mock_result.transcript = []
    mock_result.input_tokens = 50
    mock_result.output_tokens = 25
    mock_result.duration_ms = 1000

    mock_exec = mocker.AsyncMock()
    mock_exec.run.return_value = mock_result

    mocker.patch(
        "app.endpoints.agents.get_step_executor",
        return_value=mock_exec,
    )
    return mock_exec


class TestRunAgentHandler:
    """Tests for run_agent_handler."""

    @pytest.mark.asyncio
    async def test_successful_run(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Successful agent run returns result."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")

        body = AgentRunRequest(
            prompt="Analyze the cluster",
            provider="openai",
            model="gpt-4o-mini",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        result = await run_agent_handler.__wrapped__(request, body, auth)

        assert result["status"] == "completed"
        assert result["output"] == {"summary": "Done"}
        assert result["token_usage"]["input_tokens"] == 50

    @pytest.mark.asyncio
    async def test_passes_provider_and_model(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Provider and model are passed through to step input."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")

        body = AgentRunRequest(
            prompt="Hello",
            provider="openai",
            model="gpt-4o-mini",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert call_args.provider["name"] == "openai"
        assert call_args.provider["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_ephemeral_passes_real_spawner_to_executor(
        self,
        mocker: MockerFixture,
    ) -> None:
        """spawn=ephemeral with a spawner config builds and passes a real spawner.

        Regression test: get_step_executor(step_def, spawner=None) used to be
        hardcoded regardless of spawner_configuration, silently no-opping
        ephemeral spawn through this endpoint.
        """
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_cfg = mocker.patch("app.endpoints.agents.configuration")
        spawner_config = mocker.MagicMock()
        mock_cfg.spawner_configuration = spawner_config
        mock_cfg.inference.default_provider = None
        mock_cfg.inference.default_model = None

        fake_spawner = mocker.MagicMock()
        mock_build_spawner = mocker.patch(
            "app.endpoints.agents.build_spawner", return_value=fake_spawner
        )

        mock_result = mocker.MagicMock()
        mock_result.status = "completed"
        mock_result.output = {}
        mock_result.error = None
        mock_result.transcript = []
        mock_result.input_tokens = 0
        mock_result.output_tokens = 0
        mock_result.duration_ms = 0
        mock_exec = mocker.AsyncMock()
        mock_exec.run.return_value = mock_result
        mock_get_step_executor = mocker.patch(
            "app.endpoints.agents.get_step_executor", return_value=mock_exec
        )

        body = AgentRunRequest(
            prompt="Fix the issue",
            spawn="ephemeral",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        mock_build_spawner.assert_called_once_with(spawner_config)
        mock_get_step_executor.assert_called_once()
        _, kwargs = mock_get_step_executor.call_args
        assert kwargs["spawner"] is fake_spawner

    @pytest.mark.asyncio
    async def test_ephemeral_threads_sandbox_image_and_credentials(
        self,
        mocker: MockerFixture,
        mock_executor: Any,
    ) -> None:
        """spawn=ephemeral passes sandbox_image and credentials_secret through.

        Regression test: SandboxExecutor.run() reads step_input.sandbox_image
        and step_input.provider["credentials_secret"] to know which container
        image to use and which env var holds the LLM API key. Without these,
        a real sandbox spawns but can't call the LLM (no credentials) and
        uses the wrong image.
        """
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_cfg = mocker.patch("app.endpoints.agents.configuration")
        spawner_config = mocker.MagicMock()
        spawner_config.sandbox_image = "default-sandbox:latest"
        mock_cfg.spawner_configuration = spawner_config
        mock_cfg.inference.default_provider = None
        mock_cfg.inference.default_model = None
        mocker.patch(
            "app.endpoints.agents.build_spawner", return_value=mocker.MagicMock()
        )

        body = AgentRunRequest(
            prompt="Fix the issue",
            spawn="ephemeral",
            provider="openai",
            sandbox_image="custom-sandbox:v2",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert call_args.sandbox_image == "custom-sandbox:v2"
        assert call_args.provider["credentials_secret"] == "OPENAI_API_KEY"

    @pytest.mark.asyncio
    async def test_ephemeral_unknown_provider_omits_credentials_secret(
        self,
        mocker: MockerFixture,
        mock_executor: Any,
    ) -> None:
        """spawn=ephemeral with no/unknown provider omits credentials_secret.

        Regression test: a prior version defaulted to "OPENAI_API_KEY"
        regardless of provider, which would silently stamp the wrong (or a
        nonexistent) env var name onto the sandbox for any non-OpenAI or
        misspelled provider. Omitting the key lets the sandbox fail loudly
        instead of guessing.
        """
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_cfg = mocker.patch("app.endpoints.agents.configuration")
        spawner_config = mocker.MagicMock()
        spawner_config.sandbox_image = "default-sandbox:latest"
        mock_cfg.spawner_configuration = spawner_config
        mock_cfg.inference.default_provider = None
        mock_cfg.inference.default_model = None
        mocker.patch(
            "app.endpoints.agents.build_spawner", return_value=mocker.MagicMock()
        )

        body = AgentRunRequest(prompt="Fix the issue", spawn="ephemeral")
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert "credentials_secret" not in call_args.provider

    @pytest.mark.asyncio
    async def test_ephemeral_credentials_secret_matches_provider(
        self,
        mocker: MockerFixture,
        mock_executor: Any,
    ) -> None:
        """credentials_secret uses the env var matching body.provider, not always OpenAI.

        Regression test: hardcoding "OPENAI_API_KEY" regardless of provider
        would make the sandbox look for an Anthropic/Gemini/Azure key under
        the wrong env var name, so the LLM call inside the sandbox would
        fail (or silently pick up an unrelated OpenAI key) for any
        non-OpenAI provider.
        """
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_cfg = mocker.patch("app.endpoints.agents.configuration")
        spawner_config = mocker.MagicMock()
        spawner_config.sandbox_image = "default-sandbox:latest"
        mock_cfg.spawner_configuration = spawner_config
        mock_cfg.inference.default_provider = None
        mock_cfg.inference.default_model = None
        mocker.patch(
            "app.endpoints.agents.build_spawner", return_value=mocker.MagicMock()
        )

        body = AgentRunRequest(
            prompt="Fix the issue",
            spawn="ephemeral",
            provider="anthropic",
            model="claude-sonnet-5",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert call_args.provider["credentials_secret"] == "ANTHROPIC_API_KEY"

    @pytest.mark.asyncio
    async def test_ephemeral_falls_back_to_spawner_config_sandbox_image(
        self,
        mocker: MockerFixture,
        mock_executor: Any,
    ) -> None:
        """Without a request-level sandbox_image, falls back to spawner config."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_cfg = mocker.patch("app.endpoints.agents.configuration")
        spawner_config = mocker.MagicMock()
        spawner_config.sandbox_image = "default-sandbox:latest"
        mock_cfg.spawner_configuration = spawner_config
        mock_cfg.inference.default_provider = None
        mock_cfg.inference.default_model = None
        mocker.patch(
            "app.endpoints.agents.build_spawner", return_value=mocker.MagicMock()
        )

        body = AgentRunRequest(prompt="Fix the issue", spawn="ephemeral")
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert call_args.sandbox_image == "default-sandbox:latest"

    @pytest.mark.asyncio
    async def test_non_ephemeral_has_no_credentials_secret(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """spawn=none doesn't set credentials_secret (irrelevant, no sandbox)."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")

        body = AgentRunRequest(prompt="Hello", provider="openai", model="gpt-4o-mini")
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert "credentials_secret" not in call_args.provider

    @pytest.mark.asyncio
    async def test_ephemeral_without_spawner_raises(
        self,
        mocker: MockerFixture,
        mock_config: Any,
    ) -> None:
        """spawn=ephemeral without spawner config raises 400."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")

        body = AgentRunRequest(
            prompt="Fix the issue",
            spawn="ephemeral",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await run_agent_handler.__wrapped__(request, body, auth)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_falls_back_to_configured_default_provider_and_model(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Omitted provider/model fall back to inference.default_provider/model.

        Regression test: the docstring for AgentRunRequest.model promises a
        fallback to inference.default_model (mirroring
        start_workflow_handler's behavior for RunWorkflowRequest.provider),
        but the handler used to hardcode `body.provider or ""` /
        `body.model or ""` with no fallback, silently producing
        {"name": "", "model": ""} and a confusing "Unknown provider ''"
        error deep in cloud_agents even when defaults were configured.
        """
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_config.inference.default_provider = "openai"
        mock_config.inference.default_model = "gpt-4o-mini"

        body = AgentRunRequest(prompt="Analyze the cluster")
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert call_args.provider["name"] == "openai"
        assert call_args.provider["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_explicit_provider_and_model_override_configured_defaults(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Explicit provider/model in the request take priority over defaults."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_config.inference.default_provider = "openai"
        mock_config.inference.default_model = "gpt-4o-mini"

        body = AgentRunRequest(
            prompt="Analyze the cluster",
            provider="anthropic",
            model="claude-sonnet-5",
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert call_args.provider["name"] == "anthropic"
        assert call_args.provider["model"] == "claude-sonnet-5"

    @pytest.mark.asyncio
    async def test_ephemeral_credentials_secret_uses_default_provider_fallback(
        self,
        mocker: MockerFixture,
        mock_executor: Any,
    ) -> None:
        """Ephemeral credentials_secret resolution honors the fallback provider.

        Regression test: cred_secret used to be derived from raw
        `body.provider or ""`, so a configured default_provider had no
        effect on which credentials env var got threaded into the sandbox.
        """
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_cfg = mocker.patch("app.endpoints.agents.configuration")
        spawner_config = mocker.MagicMock()
        spawner_config.sandbox_image = "default-sandbox:latest"
        mock_cfg.spawner_configuration = spawner_config
        mock_cfg.inference.default_provider = "anthropic"
        mock_cfg.inference.default_model = "claude-sonnet-5"
        mocker.patch(
            "app.endpoints.agents.build_spawner", return_value=mocker.MagicMock()
        )

        body = AgentRunRequest(prompt="Fix the issue", spawn="ephemeral")
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert call_args.provider["name"] == "anthropic"
        assert call_args.provider["model"] == "claude-sonnet-5"
        assert call_args.provider["credentials_secret"] == "ANTHROPIC_API_KEY"

    @pytest.mark.asyncio
    async def test_local_spawn_dispatches_without_spawner(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """spawn=local dispatches via get_step_executor with no spawner.

        Mirrors spawn=none: SubprocessExecutor inherits the host process's
        env directly, so no spawner/credentials_secret plumbing is needed.
        """
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_build_spawner = mocker.patch("app.endpoints.agents.build_spawner")
        mock_get_step_executor = mocker.patch(
            "app.endpoints.agents.get_step_executor", return_value=mock_executor
        )

        body = AgentRunRequest(prompt="Fix the issue", spawn="local")
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        mock_build_spawner.assert_not_called()
        _, kwargs = mock_get_step_executor.call_args
        assert kwargs["spawner"] is None
        assert mock_get_step_executor.call_args[0][0]["spawn"] == "local"
        call_args = mock_executor.run.call_args[0][0]
        assert "credentials_secret" not in call_args.provider

    @pytest.mark.asyncio
    async def test_allowed_skills_reach_step_input_and_raw_step(
        self,
        mocker: MockerFixture,
        mock_executor: Any,
    ) -> None:
        """allowed_skills is threaded to StepInput and the raw step dict.

        DirectExecutor/SubprocessExecutor read step_input.allowed_skills;
        SandboxExecutor only forwards step_input.raw_step to step_runner,
        which is where spawner.spawn(allowed_skills=...) (per-skill
        Landlock grants) comes from. Both must carry the allowlist or the
        ephemeral path silently drops it.
        """
        mocker.patch("app.endpoints.agents.check_configuration_loaded")
        mock_cfg = mocker.patch("app.endpoints.agents.configuration")
        spawner_config = mocker.MagicMock()
        spawner_config.sandbox_image = "default-sandbox:latest"
        mock_cfg.spawner_configuration = spawner_config
        mock_cfg.inference.default_provider = None
        mock_cfg.inference.default_model = None
        mocker.patch(
            "app.endpoints.agents.build_spawner", return_value=mocker.MagicMock()
        )

        body = AgentRunRequest(
            prompt="Diagnose the pod",
            spawn="ephemeral",
            provider="openai",
            model="gpt-4o-mini",
            allowed_skills=["k8s-diag"],
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert call_args.allowed_skills == ["k8s-diag"]
        assert call_args.raw_step["allowed_skills"] == ["k8s-diag"]
        assert call_args.raw_step["name"] == "agent-run"

    @pytest.mark.asyncio
    async def test_raw_step_selects_request_mcp_servers_by_name(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """raw_step lists request MCP server names for step_runner injection.

        step_runner only injects catalog entries (StepInput.mcp_servers)
        whose names appear in step["mcp_servers"] -- without the name list,
        LIGHTSPEED_MCP_SERVERS is never set and the sandbox agent has no
        MCP tools to call.
        """
        mocker.patch("app.endpoints.agents.check_configuration_loaded")

        body = AgentRunRequest(
            prompt="Check the pod",
            provider="openai",
            model="gpt-4o-mini",
            mcp_servers=[{"name": "pod-status", "url": "http://x/mcp"}],
        )
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert call_args.raw_step["mcp_servers"] == ["pod-status"]

    @pytest.mark.asyncio
    async def test_allowed_skills_omitted_means_no_skills(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """Omitted allowed_skills stays None (no skills), not empty filtering."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")

        body = AgentRunRequest(prompt="Hello", provider="openai", model="gpt-4o-mini")
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        await run_agent_handler.__wrapped__(request, body, auth)

        call_args = mock_executor.run.call_args[0][0]
        assert call_args.allowed_skills is None
        assert call_args.raw_step["allowed_skills"] is None

    @pytest.mark.asyncio
    async def test_local_spawn_result_shape(
        self,
        mocker: MockerFixture,
        mock_config: Any,
        mock_executor: Any,
    ) -> None:
        """spawn=local returns the same result shape as spawn=none."""
        mocker.patch("app.endpoints.agents.check_configuration_loaded")

        body = AgentRunRequest(prompt="Fix the issue", spawn="local")
        auth = ("user-1", "testuser", False, "token")
        request = mocker.MagicMock()

        result = await run_agent_handler.__wrapped__(request, body, auth)

        assert result["status"] == "completed"
        assert result["output"] == {"summary": "Done"}
