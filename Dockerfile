# The public booking MCP server: one process, one worker, streamable HTTP at /mcp.
# Configuration comes from the host's environment at runtime, never from the image.
# Build: docker build --build-arg SA_GIT_COMMIT=$(git rev-parse HEAD) -t second-audience .

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first, in their own layer, so code changes do not reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
# Non-editable, so the runtime stage needs only the virtualenv, not the source tree.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable


FROM python:3.12-slim
RUN useradd --create-home --uid 1000 app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# Render passes RENDER_GIT_COMMIT to the build; an explicit SA_GIT_COMMIT build arg wins.
ARG RENDER_GIT_COMMIT=
ARG SA_GIT_COMMIT=${RENDER_GIT_COMMIT}
ENV SA_GIT_COMMIT=${SA_GIT_COMMIT} \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PORT=10000

USER app
WORKDIR /home/app
EXPOSE 10000
# A shell is needed only to expand $PORT, which the host sets; exec keeps Python as PID 1.
CMD ["sh", "-c", "exec python -m second_audience.booking.server --transport http --host 0.0.0.0 --port \"$PORT\""]
