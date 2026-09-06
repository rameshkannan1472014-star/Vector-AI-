# ============================================================
# ENGINEER AI BACKEND
# MILESTONE 3
# PART 2/5
# ============================================================


# ============================================================
# COMMAND SECURITY
# ============================================================

SAFE_COMMANDS = {
    "python",
    "python3",
    "pytest",
    "node",
    "npm",
}


def validate_command(
    command: str,
) -> str:

    command = (command or "").strip()

    if not command:
        raise ValueError(
            "Command is required."
        )

    if "/" in command or "\\" in command:
        raise ValueError(
            "Command paths are not allowed."
        )

    command_name = Path(command).name.lower()

    if command_name not in SAFE_COMMANDS:
        raise ValueError(
            f"Command is not allowed: {command_name}"
        )

    return command_name


def clamp_execution_output(
    value: bytes,
) -> str:

    text = value.decode(
        "utf-8",
        errors="replace",
    )

    return clamp_text(
        text,
        MAX_EXECUTION_OUTPUT_CHARS,
    )


def create_execution_workspace() -> str:

    base = Path(
        os.getenv(
            "EXECUTION_TMP_DIR",
            "/tmp",
        )
    )

    base.mkdir(
        parents=True,
        exist_ok=True,
    )

    workspace = base / (
        "engineer-ai-"
        + uuid.uuid4().hex
    )

    workspace.mkdir(
        parents=True,
        exist_ok=False,
    )

    return str(workspace)


def cleanup_execution_workspace(
    workspace: str,
) -> None:

    try:
        shutil.rmtree(
            workspace,
            ignore_errors=True,
        )
    except Exception:
        logger.exception(
            "Workspace cleanup failed."
        )


def write_execution_files(
    workspace: str,
    files: List[ExecutionFile],
) -> None:

    total_size = 0

    for item in files:

        relative = safe_relative_path(
            item.path
        )

        target = (
            Path(workspace)
            / relative
        )

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        content = item.content

        total_size += len(content)

        if total_size > MAX_EXECUTION_PROJECT_SIZE:
            raise ValueError(
                "Execution project exceeds size limit."
            )

        target.write_text(
            content,
            encoding="utf-8",
        )


def build_execution_environment() -> Dict[str, str]:

    # Do NOT copy the complete server environment.
    # This intentionally avoids exposing API keys and secrets.
    return {
        "PATH": os.getenv(
            "PATH",
            "/usr/local/bin:/usr/bin:/bin",
        ),
        "HOME": "/tmp",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NODE_ENV": "test",
        "CI": "1",
    }


def execution_image_for_language(
    language: str,
) -> str:

    lang = (language or "python").lower()

    if lang in {
        "javascript",
        "js",
        "typescript",
        "ts",
        "node",
    }:
        return DEFAULT_NODE_IMAGE

    return DEFAULT_PYTHON_IMAGE


def normalize_command_for_language(
    command: str,
) -> str:

    command = validate_command(
        command
    )

    if command == "python3":
        return "python"

    return command


# ============================================================
# HOST PROCESS EXECUTION
# ============================================================

async def run_host_process(
    workspace: str,
    command: str,
    args: List[str],
    timeout_seconds: int,
) -> Dict[str, Any]:

    executable = normalize_command_for_language(
        command
    )

    env = build_execution_environment()

    full_command = [
        executable,
        *args,
    ]

    start = time.perf_counter()

    process = await asyncio.create_subprocess_exec(
        *full_command,
        cwd=workspace,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    try:

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )

        latency = (
            time.perf_counter()
            - start
        )

        return {
            "status": (
                "success"
                if process.returncode == 0
                else "failed"
            ),
            "exit_code": process.returncode,
            "stdout": clamp_execution_output(stdout),
            "stderr": clamp_execution_output(stderr),
            "timed_out": False,
            "latency_seconds": round(
                latency,
                3,
            ),
        }

    except asyncio.TimeoutError:

        try:
            os.killpg(
                os.getpgid(process.pid),
                signal.SIGKILL,
            )
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

        await process.communicate()

        latency = (
            time.perf_counter()
            - start
        )

        return {
            "status": "timeout",
            "exit_code": None,
            "stdout": "",
            "stderr": (
                "Execution timed out after "
                f"{timeout_seconds} seconds."
            ),
            "timed_out": True,
            "latency_seconds": round(
                latency,
                3,
            ),
        }


# ============================================================
# DOCKER EXECUTION
# ============================================================

async def run_docker_process(
    workspace: str,
    command: str,
    args: List[str],
    timeout_seconds: int,
    language: str,
) -> Dict[str, Any]:

    docker = shutil.which(
        "docker"
    )

    if not docker:
        raise RuntimeError(
            "Docker is not available on this server. "
            "Use a deployment environment with Docker "
            "or configure a dedicated sandbox service."
        )

    command = normalize_command_for_language(
        command
    )

    image = execution_image_for_language(
        language
    )

    container_name = (
        "engineer-ai-"
        + uuid.uuid4().hex[:20]
    )

    docker_command = [
        docker,
        "run",
        "--rm",
        "--name",
        container_name,

        "--network",
        "none",

        "--cpus",
        "0.5",

        "--memory",
        "256m",

        "--pids-limit",
        "64",

        "--read-only",

        "--tmpfs",
        "/tmp:rw,nosuid,size=64m",

        "-v",
        f"{workspace}:/workspace:rw",

        "-w",
        "/workspace",

        "--security-opt",
        "no-new-privileges",

        image,
        command,
        *args,
    ]

    env = build_execution_environment()

    start = time.perf_counter()

    process = await asyncio.create_subprocess_exec(
        *docker_command,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )

        latency = (
            time.perf_counter()
            - start
        )

        return {
            "status": (
                "success"
                if process.returncode == 0
                else "failed"
            ),
            "exit_code": process.returncode,
            "stdout": clamp_execution_output(stdout),
            "stderr": clamp_execution_output(stderr),
            "timed_out": False,
            "latency_seconds": round(
                latency,
                3,
            ),
        }

    except asyncio.TimeoutError:

        # Try to remove the container if it is still alive.
        try:
            cleanup = await asyncio.create_subprocess_exec(
                docker,
                "rm",
                "-f",
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            await asyncio.wait_for(
                cleanup.communicate(),
                timeout=5,
            )

        except Exception:
            logger.exception(
                "Failed to clean timed-out Docker container."
            )

        try:
            process.kill()
        except Exception:
            pass

        await process.communicate()

        latency = (
            time.perf_counter()
            - start
        )

        return {
            "status": "timeout",
            "exit_code": None,
            "stdout": "",
            "stderr": (
                "Sandbox execution timed out after "
                f"{timeout_seconds} seconds."
            ),
            "timed_out": True,
            "latency_seconds": round(
                latency,
                3,
            ),
        }


# ============================================================
# MAIN EXECUTION ENGINE
# ============================================================

async def execute_files(
    files: List[ExecutionFile],
    command: str,
    args: List[str],
    timeout_seconds: int,
    language: str,
) -> Dict[str, Any]:

    validate_execution_files(
        files
    )

    command = validate_command(
        command
    )

    workspace = create_execution_workspace()

    try:

        write_execution_files(
            workspace,
            files,
        )

        if EXECUTION_MODE == "docker":

            result = await run_docker_process(
                workspace=workspace,
                command=command,
                args=args,
                timeout_seconds=timeout_seconds,
                language=language,
            )

        elif EXECUTION_MODE == "host":

            # Host execution is intentionally opt-in.
            if os.getenv(
                "ALLOW_UNSANDBOXED_EXECUTION",
                "false",
            ).lower() != "true":
                raise RuntimeError(
                    "Host execution is disabled. "
                    "Set ALLOW_UNSANDBOXED_EXECUTION=true "
                    "only on a trusted isolated machine."
                )

            result = await run_host_process(
                workspace=workspace,
                command=command,
                args=args,
                timeout_seconds=timeout_seconds,
            )

        else:

            raise RuntimeError(
                "Unsupported EXECUTION_MODE. "
                "Use 'docker' or 'host'."
            )

        return result

    finally:

        cleanup_execution_workspace(
            workspace
        )


# ============================================================
# DEFAULT TEST COMMANDS
# ============================================================

def default_test_command(
    language: str,
) -> Dict[str, Any]:

    lang = (
        language or "python"
    ).lower()

    if lang in {
        "javascript",
        "js",
        "typescript",
        "ts",
        "node",
    }:
        return {
            "command": "npm",
            "args": ["test"],
        }

    return {
        "command": "python",
        "args": [
            "-m",
            "unittest",
            "discover",
            "-v",
        ],
    }


# ============================================================
# EXECUTION ENDPOINT
# ============================================================

@app.post("/api/execute")
async def execute_code(
    request: ExecuteRequest,
):

    start = time.perf_counter()

    analytics_data[
        "execution_runs"
    ] += 1

    try:

        result = await execute_files(
            files=request.files,
            command=request.command,
            args=request.args,
            timeout_seconds=request.timeout_seconds,
            language=request.language,
        )

        latency = (
            time.perf_counter()
            - start
        )

        analytics_data[
            "total_execution_latency_seconds"
        ] += latency

        if result["status"] == "success":

            analytics_data[
                "execution_successes"
            ] += 1

        elif result["timed_out"]:

            analytics_data[
                "execution_timeouts"
            ] += 1

        else:

            analytics_data[
                "execution_failures"
            ] += 1

        record(
            start,
            result["status"] == "success",
        )

        return {
            "status": result["status"],
            "execution": result,
            "executed": True,
            "sandbox": EXECUTION_MODE,
        }

    except ValueError as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception as exc:

        record(
            start,
            False,
        )

        logger.exception(
            "Execution failed."
        )

        raise HTTPException(
            status_code=503,
            detail={
                "error": "Execution unavailable",
                "message": str(exc),
            },
        )


# ============================================================
# TEST ENDPOINT
# ============================================================

@app.post("/api/test/run")
async def run_tests(
    request: TestRequest,
):

    start = time.perf_counter()

    analytics_data[
        "test_runs"
    ] += 1

    try:

        selected = default_test_command(
            request.language
        )

        command = (
            request.test_command
            or selected["command"]
        )

        args = (
            request.test_args
            if request.test_args
            else selected["args"]
        )

        result = await execute_files(
            files=request.files,
            command=command,
            args=args,
            timeout_seconds=request.timeout_seconds,
            language=request.language,
        )

        latency = (
            time.perf_counter()
            - start
        )

        analytics_data[
            "total_test_latency_seconds"
        ] += latency

        if result["status"] == "success":
            analytics_data[
                "test_successes"
            ] += 1
        else:
            analytics_data[
                "test_failures"
            ] += 1

        record(
            start,
            result["status"] == "success",
        )

        return {
            "status": result["status"],
            "tests": result,
            "executed": True,
            "command": command,
            "args": args,
            "sandbox": EXECUTION_MODE,
        }

    except ValueError as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception as exc:

        record(
            start,
            False,
        )

        logger.exception(
            "Test execution failed."
        )

        raise HTTPException(
            status_code=503,
            detail={
                "error": "Test execution unavailable",
                "message": str(exc),
            },
        )
# ============================================================
# ENGINEER AI BACKEND
# MILESTONE 3
# PART 3/5
# ============================================================


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():

    index_path = Path(
        os.getenv(
            "FRONTEND_INDEX",
            "index.html",
        )
    )

    if index_path.exists():

        return FileResponse(
            index_path
        )

    return {
        "name": APP_TITLE,
        "version": APP_VERSION,
        "status": "online",
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
async def health():

    return {
        "status": "healthy",
        "service": APP_TITLE,
        "version": APP_VERSION,
        "provider": DEFAULT_PROVIDER,
        "model": DEFAULT_MODEL,
        "execution_mode": EXECUTION_MODE,
        "docker_available": bool(
            shutil.which("docker")
        ),
    }


# ============================================================
# CHAT
# ============================================================

@app.post("/api/chat")
async def chat(
    request: ChatRequest,
):

    start = time.perf_counter()

    try:

        prompt = clamp_text(
            request.prompt,
            MAX_PROMPT_CHARS,
        )

        output = await infer(
            prompt,
            CHAT_MAX_OUTPUT_TOKENS,
        )

        record(
            start,
            True,
        )

        return {
            "status": "success",
            "provider": request.provider,
            "model": DEFAULT_MODEL,
            "response": output,
            "output": output,
            "latency_seconds": round(
                time.perf_counter() - start,
                3,
            ),
        }

    except Exception as exc:

        record(
            start,
            False,
        )

        logger.exception(
            "Chat failed."
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Chat request failed",
                "message": str(exc),
            },
        )


# ============================================================
# STANDARD MULTI-AGENT
# ============================================================

@app.post("/api/agent/execute")
async def execute_multi_agent(
    request: MultiAgentRequest,
):

    start = time.perf_counter()

    try:

        task = request.get_task()

        analytics_data[
            "agent_tasks_executed"
        ] += 1

        context = request.project_context or ""

        planner = await run_agent(
            "Planner Agent",
            (
                "Create a clear implementation plan for "
                f"this task:\n{task}"
            ),
            context,
        )

        executor = await run_agent(
            "Executor Agent",
            (
                "Design the implementation for this task:\n"
                f"{task}\n\n"
                "Provide the important code changes and explain "
                "how they should be integrated."
            ),
            context,
        )

        tester = ""
        security = ""

        async def test_agent():

            return await run_agent(
                "Testing Agent",
                (
                    "Design comprehensive tests for:\n"
                    f"{task}\n\n"
                    "Include unit tests, integration tests, "
                    "edge cases, invalid inputs, regression "
                    "cases, and expected results."
                ),
                context,
            )

        async def security_agent():

            return await run_agent(
                "Security Agent",
                (
                    "Perform a static security review for:\n"
                    f"{task}\n\n"
                    "Check authentication, authorization, "
                    "injection, secrets, unsafe input, "
                    "resource abuse, configuration, and "
                    "data leakage."
                ),
                context,
            )

        tasks = []

        if request.include_tests:
            tasks.append(
                test_agent()
            )

        if request.include_security_scan:
            tasks.append(
                security_agent()
            )

        results = []

        if tasks:
            results = await asyncio.gather(
                *tasks
            )

        index = 0

        if request.include_tests:
            tester = results[index]
            index += 1

        if request.include_security_scan:
            security = results[index]

        debugger = ""

        if request.include_debugger:

            debugger = await run_agent(
                "Debugger Agent",
                (
                    "Review the planner, implementation, "
                    "tests, and security analysis for this task. "
                    "Find inconsistencies, missing cases, "
                    "likely bugs, and recommended fixes."
                ),
                (
                    f"TASK:\n{task}\n\n"
                    f"PLAN:\n{planner}\n\n"
                    f"IMPLEMENTATION:\n{executor}\n\n"
                    f"TESTS:\n{tester}\n\n"
                    f"SECURITY:\n{security}"
                ),
            )

        record(
            start,
            True,
        )

        return {
            "status": "success",

            "workflow": {
                "task": task,
                "planner": planner,
                "executor": executor,
                "tester": tester,
                "security": security,
                "debugger": debugger,
            },

            "agent_outputs": {
                "plan": planner,
                "implementation": executor,
                "tests": tester,
                "security_scan": security,
                "debugger": debugger,
            },

            "latency_seconds": round(
                time.perf_counter() - start,
                3,
            ),
        }

    except ValueError as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception as exc:

        record(
            start,
            False,
        )

        logger.exception(
            "Multi-agent execution failed."
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Multi-agent execution failed",
                "message": str(exc),
            },
        )


# ============================================================
# CODE REVIEW
# ============================================================

@app.post("/api/code/review")
async def code_review(
    request: CodeReviewRequest,
):

    start = time.perf_counter()

    try:

        prompt = (
            "You are Engineer AI Code Review Agent.\n\n"
            f"LANGUAGE: {request.language}\n\n"
            "Review the following code carefully.\n\n"
            f"CODE:\n"
            f"```{request.language}\n"
            f"{request.code_snippet}\n"
            "```\n\n"
            "Analyze:\n"
            "1. Syntax errors\n"
            "2. Logic bugs\n"
            "3. Runtime errors\n"
            "4. Security problems\n"
            "5. Performance problems\n"
            "6. Maintainability issues\n"
            "7. Edge cases\n\n"
            "For every important problem provide:\n"
            "- problem\n"
            "- root cause\n"
            "- corrected code\n"
            "- explanation\n\n"
            "Do not claim that the code was executed."
        )

        output = await infer(
            prompt,
            REVIEW_MAX_OUTPUT_TOKENS,
        )

        record(
            start,
            True,
        )

        return {
            "status": "success",
            "review": {
                "language": request.language,
                "output": output,
            },
            "executed": False,
            "latency_seconds": round(
                time.perf_counter() - start,
                3,
            ),
        }

    except Exception as exc:

        record(
            start,
            False,
        )

        logger.exception(
            "Code review failed."
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Code review failed",
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT PROMPT
# ============================================================

def make_project_prompt(
    agent_name: str,
    instruction: str,
    context: str,
) -> str:

    return (
        f"You are the {agent_name}.\n\n"
        f"PROJECT CONTEXT:\n"
        f"{context}\n\n"
        f"YOUR TASK:\n"
        f"{instruction}\n\n"
        "IMPORTANT:\n"
        "Analyze the supplied project context only. "
        "Do not claim that code was executed unless "
        "the backend actually executed it."
    )


# ============================================================
# PROJECT ANALYZE
# ============================================================

@app.post("/api/project/analyze")
async def project_analyze(
    request: ProjectRequest,
):

    start = time.perf_counter()

    try:

        validate_project_files(
            request.files
        )

        context = build_project_context(
            request.files
        )

        analytics_data[
            "project_analysis_runs"
        ] += 1

        output = await infer(
            make_project_prompt(
                "Project Analysis Agent",
                (
                    "Analyze the complete project. "
                    "Explain its purpose, major components, "
                    "file responsibilities, architecture, "
                    "data flow, APIs, configuration, "
                    "dependencies, risks, technical debt, "
                    "and improvement opportunities."
                ),
                context,
            ),
            AGENT_MAX_OUTPUT_TOKENS,
        )

        record(
            start,
            True,
        )

        return {
            "status": "success",
            "project": request.project_name,
            "files_analyzed": len(
                request.files
            ),
            "analysis": output,
            "latency_seconds": round(
                time.perf_counter() - start,
                3,
            ),
        }

    except ValueError as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Project analysis failed",
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT ARCHITECTURE
# ============================================================

@app.post("/api/project/architecture")
async def project_architecture(
    request: ProjectRequest,
):

    start = time.perf_counter()

    try:

        validate_project_files(
            request.files
        )

        context = build_project_context(
            request.files
        )

        analytics_data[
            "architecture_runs"
        ] += 1

        output = await infer(
            make_project_prompt(
                "Architecture Agent",
                (
                    "Describe the project's architecture. "
                    "Identify components, relationships, "
                    "data flow, APIs, storage, external services, "
                    "deployment architecture, risks, and "
                    "recommended improvements. "
                    "Use Mermaid when useful."
                ),
                context,
            ),
            AGENT_MAX_OUTPUT_TOKENS,
        )

        record(
            start,
            True,
        )

        return {
            "status": "success",
            "architecture": output,
            "latency_seconds": round(
                time.perf_counter() - start,
                3,
            ),
        }

    except Exception as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Architecture analysis failed",
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT DEPENDENCIES
# ============================================================

@app.post("/api/project/dependencies")
async def project_dependencies(
    request: ProjectRequest,
):

    start = time.perf_counter()

    try:

        validate_project_files(
            request.files
        )

        context = build_project_context(
            request.files
        )

        analytics_data[
            "dependency_analysis_runs"
        ] += 1

        output = await infer(
            make_project_prompt(
                "Dependency Analysis Agent",
                (
                    "Identify direct, framework, runtime, "
                    "and development dependencies. "
                    "Identify external services, visible versions, "
                    "dependency risks, unnecessary dependencies, "
                    "upgrade opportunities, and compatibility issues. "
                    "Never invent a version that is not visible."
                ),
                context,
            ),
            AGENT_MAX_OUTPUT_TOKENS,
        )

        record(
            start,
            True,
        )

        return {
            "status": "success",
            "dependencies": output,
            "latency_seconds": round(
                time.perf_counter() - start,
                3,
            ),
        }

    except Exception as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Dependency analysis failed",
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT DEBUG
# ============================================================

@app.post("/api/project/debug")
async def project_debug(
    request: ProjectRequest,
):

    start = time.perf_counter()

    try:

        validate_project_files(
            request.files
        )

        context = build_project_context(
            request.files
        )

        analytics_data[
            "debugger_runs"
        ] += 1

        output = await infer(
            make_project_prompt(
                "Project Debugger Agent",
                (
                    "Perform static debugging. "
                    "Find syntax issues, logic bugs, "
                    "bad assumptions, data-flow problems, "
                    "error-handling problems, configuration "
                    "issues, integration problems, and likely "
                    "runtime failures. Give root causes, fixes, "
                    "and regression risks."
                ),
                context,
            ),
            AGENT_MAX_OUTPUT_TOKENS,
        )

        record(
            start,
            True,
        )

        return {
            "status": "success",
            "debug": output,
            "executed": False,
            "latency_seconds": round(
                time.perf_counter() - start,
                3,
            ),
        }

    except Exception as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Project debugging failed",
                "message": str(exc),
            },
        )
# ============================================================
# ENGINEER AI BACKEND
# MILESTONE 3
# PART 4/5
# ============================================================


# ============================================================
# PROJECT TEST GENERATOR
# ============================================================

@app.post("/api/project/tests")
async def project_tests(
    request: ProjectRequest,
):

    start = time.perf_counter()

    try:

        validate_project_files(
            request.files
        )

        context = build_project_context(
            request.files
        )

        analytics_data[
            "tests_generated"
        ] += 1

        output = await infer(
            make_project_prompt(
                "Project Testing Agent",
                (
                    "Generate a comprehensive test strategy. "
                    "Include unit tests, integration tests, "
                    "edge cases, invalid inputs, regression "
                    "cases, assertions, target files, and "
                    "example test code. "
                    "Do not claim that tests were executed."
                ),
                context,
            ),
            AGENT_MAX_OUTPUT_TOKENS,
        )

        record(
            start,
            True,
        )

        return {
            "status": "success",
            "tests": output,
            "executed": False,
            "latency_seconds": round(
                time.perf_counter() - start,
                3,
            ),
        }

    except Exception as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Test generation failed",
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT SECURITY
# ============================================================

@app.post("/api/project/security")
async def project_security(
    request: ProjectRequest,
):

    start = time.perf_counter()

    try:

        validate_project_files(
            request.files
        )

        context = build_project_context(
            request.files
        )

        analytics_data[
            "security_scans_completed"
        ] += 1

        output = await infer(
            make_project_prompt(
                "Project Security Agent",
                (
                    "Perform a static security review. "
                    "Check authentication, authorization, "
                    "injection, secrets, file operations, "
                    "configuration, dependencies, data leakage, "
                    "resource abuse, input validation, logging, "
                    "and other vulnerabilities. "
                    "For each issue provide severity, root cause, "
                    "affected file, and mitigation. "
                    "Do not claim a live security scan."
                ),
                context,
            ),
            AGENT_MAX_OUTPUT_TOKENS,
        )

        record(
            start,
            True,
        )

        return {
            "status": "success",
            "security": output,
            "executed": False,
            "latency_seconds": round(
                time.perf_counter() - start,
                3,
            ),
        }

    except Exception as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Security analysis failed",
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT PERFORMANCE
# ============================================================

@app.post("/api/project/performance")
async def project_performance(
    request: ProjectRequest,
):

    start = time.perf_counter()

    try:

        validate_project_files(
            request.files
        )

        context = build_project_context(
            request.files
        )

        analytics_data[
            "performance_analyses"
        ] += 1

        output = await infer(
            make_project_prompt(
                "Project Performance Agent",
                (
                    "Analyze performance. "
                    "Check slow algorithms, repeated work, "
                    "inefficient loops, memory usage, blocking "
                    "operations, network requests, database "
                    "inefficiencies, caching, frontend performance, "
                    "and scalability. "
                    "Give practical optimizations."
                ),
                context,
            ),
            AGENT_MAX_OUTPUT_TOKENS,
        )

        record(
            start,
            True,
        )

        return {
            "status": "success",
            "performance": output,
            "executed": False,
            "latency_seconds": round(
                time.perf_counter() - start,
                3,
            ),
        }

    except Exception as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Performance analysis failed",
                "message": str(exc),
            },
        )


# ============================================================
# PROJECT MULTI-AGENT
# ============================================================

@app.post("/api/project/agent")
async def project_agent(
    request: ProjectAgentRequest,
):

    start = time.perf_counter()

    try:

        validate_project_files(
            request.files
        )

        context = build_project_context(
            request.files
        )

        analytics_data[
            "agent_tasks_executed"
        ] += 1

        planner = await infer(
            make_project_prompt(
                "Project Planner Agent",
                (
                    f"Plan how to accomplish this task:\n"
                    f"{request.task}\n\n"
                    "Identify affected files, architecture "
                    "changes, implementation steps, risks, "
                    "and verification steps."
                ),
                context,
            ),
            AGENT_MAX_OUTPUT_TOKENS,
        )

        executor = await infer(
            make_project_prompt(
                "Project Executor Agent",
                (
                    f"Design the actual implementation for:\n"
                    f"{request.task}\n\n"
                    "Provide precise code changes and explain "
                    "which files should be modified."
                ),
                (
                    f"{context}\n\n"
                    f"PLANNER OUTPUT:\n{planner}"
                ),
            ),
            AGENT_MAX_OUTPUT_TOKENS,
        )

        async def project_test():

            return await infer(
                make_project_prompt(
                    "Project Tester Agent",
                    (
                        "Design tests for the requested change. "
                        "Cover normal behavior, edge cases, "
                        "invalid inputs, and regression cases."
                    ),
                    context,
                ),
                AGENT_MAX_OUTPUT_TOKENS,
            )

        async def project_security():

            return await infer(
                make_project_prompt(
                    "Project Security Agent",
                    (
                        "Review the requested change for "
                        "security problems and give mitigations."
                    ),
                    context,
                ),
                AGENT_MAX_OUTPUT_TOKENS,
            )

        async def project_performance():

            return await infer(
                make_project_prompt(
                    "Project Performance Agent",
                    (
                        "Review the requested change for "
                        "performance and scalability problems."
                    ),
                    context,
                ),
                AGENT_MAX_OUTPUT_TOKENS,
            )

        parallel_tasks = []
        labels = []

        if request.include_tests:
            parallel_tasks.append(
                project_test()
            )
            labels.append("tester")

        if request.include_security:
            parallel_tasks.append(
                project_security()
            )
            labels.append("security")

        if request.include_performance:
            parallel_tasks.append(
                project_performance()
            )
            labels.append("performance")

        parallel_results = []

        if parallel_tasks:

            parallel_results = await asyncio.gather(
                *parallel_tasks
            )

        tester = ""
        security = ""
        performance = ""

        for label, result in zip(
            labels,
            parallel_results,
        ):

            if label == "tester":
                tester = result

            elif label == "security":
                security = result

            elif label == "performance":
                performance = result

        debugger = await infer(
            make_project_prompt(
                "Project Debugger Agent",
                (
                    "Review all agent outputs. "
                    "Find contradictions, missing implementation "
                    "steps, bugs, security risks, test gaps, and "
                    "performance concerns. Give a final correction "
                    "plan."
                ),
                (
                    f"TASK:\n{request.task}\n\n"
                    f"PLANNER:\n{planner}\n\n"
                    f"EXECUTOR:\n{executor}\n\n"
                    f"TESTER:\n{tester}\n\n"
                    f"SECURITY:\n{security}\n\n"
                    f"PERFORMANCE:\n{performance}"
                ),
            ),
            AGENT_MAX_OUTPUT_TOKENS,
        )

        record(
            start,
            True,
        )

        return {
            "status": "success",
            "project": request.project_name,

            "workflow": {
                "task": request.task,
                "project": request.project_name,
                "planner": planner,
                "executor": executor,
                "tester": tester,
                "security": security,
                "performance": performance,
                "debugger": debugger,
            },

            "agent_outputs": {
                "plan": planner,
                "implementation": executor,
                "tests": tester,
                "security_scan": security,
                "performance": performance,
                "debugger": debugger,
            },

            "execution": {
                "status": "not_executed",
                "message": (
                    "The project was analyzed statically. "
                    "No project code was executed."
                ),
            },

            "latency_seconds": round(
                time.perf_counter() - start,
                3,
            ),
        }

    except Exception as exc:

        record(
            start,
            False,
        )

        logger.exception(
            "Project agent failed."
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Project agent failed",
                "message": str(exc),
            },
        )


# ============================================================
# AUTO-FIX JSON PARSER
# ============================================================

def parse_json_response(
    text: str,
) -> Dict[str, Any]:

    text = (text or "").strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    try:

        return json.loads(
            text
        )

    except json.JSONDecodeError:

        start = text.find("{")
        end = text.rfind("}")

        if start >= 0 and end > start:

            return json.loads(
                text[start:end + 1]
            )

        raise


def validate_changed_files(
    changes: List[Dict[str, Any]],
) -> None:

    if len(changes) > MAX_EXECUTION_FILES:
        raise ValueError(
            "AI returned too many changed files."
        )

    total = 0

    for change in changes:

        if not isinstance(change, dict):
            raise ValueError(
                "Invalid change object."
            )

        path = safe_relative_path(
            str(change.get("path", ""))
        )

        content = change.get(
            "content"
        )

        if not isinstance(
            content,
            str,
        ):
            raise ValueError(
                f"Invalid content for {path}."
            )

        if len(content) > MAX_EXECUTION_FILE_SIZE:
            raise ValueError(
                f"Changed file is too large: {path}"
            )

        total += len(content)

    if total > MAX_EXECUTION_PROJECT_SIZE:
        raise ValueError(
            "AI-generated changes are too large."
        )


def apply_changes(
    files: List[ExecutionFile],
    changes: List[Dict[str, Any]],
) -> List[ExecutionFile]:

    validate_changed_files(
        changes
    )

    mapping = {
        safe_relative_path(item.path): item.content
        for item in files
    }

    for change in changes:

        path = safe_relative_path(
            str(change["path"])
        )

        mapping[path] = change["content"]

    return [
        ExecutionFile(
            path=path,
            content=content,
        )
        for path, content in mapping.items()
    ]


# ============================================================
# AUTO-FIX AI
# ============================================================

async def generate_auto_fix(
    task: str,
    language: str,
    files: List[ExecutionFile],
    test_result: Dict[str, Any],
) -> Dict[str, Any]:

    context = build_execution_context(
        files
    )

    prompt = (
        "You are Engineer AI Auto-Fix Agent.\n\n"

        "Your job is to diagnose the failed execution/test "
        "and return only the files that need to change.\n\n"

        f"TASK:\n{task}\n\n"

        f"LANGUAGE:\n{language}\n\n"

        f"PROJECT:\n{context}\n\n"

        "EXECUTION RESULT:\n"
        f"{json.dumps(test_result, indent=2)}\n\n"

        "Rules:\n"
        "1. Find the most likely root cause.\n"
        "2. Fix the actual problem rather than hiding the failure.\n"
        "3. Preserve existing behavior where possible.\n"
        "4. Do not invent files unnecessarily.\n"
        "5. Return complete contents for changed files.\n"
        "6. Return valid JSON only.\n\n"

        "Required JSON format:\n"
        "{\n"
        '  "diagnosis": "root cause",\n'
        '  "summary": "what was fixed",\n'
        '  "changes": [\n'
        "    {\n"
        '      "path": "file.py",\n'
        '      "content": "complete file content"\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )

    raw = await infer(
        prompt,
        AUTO_FIX_MAX_OUTPUT_TOKENS,
        json_mode=True,
    )

    return parse_json_response(
        raw
    )


# ============================================================
# AUTO-FIX LOOP
# ============================================================

async def run_auto_fix_loop(
    request: AutoFixRequest,
) -> Dict[str, Any]:

    start = time.perf_counter()

    analytics_data[
        "auto_fix_runs"
    ] += 1

    current_files = list(
        request.files
    )

    selected = default_test_command(
        request.language
    )

    command = (
        request.test_command
        or selected["command"]
    )

    args = (
        request.test_args
        if request.test_args
        else selected["args"]
    )

    history = []

    for cycle in range(
        1,
        request.max_cycles + 1,
    ):

        test_result = await execute_files(
            files=current_files,
            command=command,
            args=args,
            timeout_seconds=request.timeout_seconds,
            language=request.language,
        )

        history.append({
            "cycle": cycle,
            "execution": test_result,
        })

        if test_result["status"] == "success":

            latency = (
                time.perf_counter()
                - start
            )

            analytics_data[
                "total_auto_fix_latency_seconds"
            ] += latency

            analytics_data[
                "auto_fix_successes"
            ] += 1

            return {
                "status": "success",
                "message": (
                    "Tests passed successfully."
                ),
                "cycles": cycle,
                "files": [
                    item.model_dump()
                    for item in current_files
                ],
                "history": history,
                "execution": test_result,
                "fixed": cycle > 1,
                "latency_seconds": round(
                    latency,
                    3,
                ),
            }

        fix_result = await generate_auto_fix(
            task=request.task,
            language=request.language,
            files=current_files,
            test_result=test_result,
        )

        changes = fix_result.get(
            "changes",
            [],
        )

        if not changes:

            latency = (
                time.perf_counter()
                - start
            )

            analytics_data[
                "total_auto_fix_latency_seconds"
            ] += latency

            analytics_data[
                "auto_fix_failures"
            ] += 1

            return {
                "status": "failed",
                "message": (
                    "The AI could not produce a safe file change "
                    "for the failing test."
                ),
                "cycles": cycle,
                "diagnosis": fix_result.get(
                    "diagnosis",
                    "",
                ),
                "summary": fix_result.get(
                    "summary",
                    "",
                ),
                "files": [
                    item.model_dump()
                    for item in current_files
                ],
                "history": history,
                "fixed": False,
                "latency_seconds": round(
                    latency,
                    3,
                ),
            }

        current_files = apply_changes(
            current_files,
            changes,
        )

        history[-1]["fix"] = {
            "diagnosis": fix_result.get(
                "diagnosis",
                "",
            ),
            "summary": fix_result.get(
                "summary",
                "",
            ),
            "changed_files": [
                safe_relative_path(
                    str(item["path"])
                )
                for item in changes
            ],
        }

    final_result = await execute_files(
        files=current_files,
        command=command,
        args=args,
        timeout_seconds=request.timeout_seconds,
        language=request.language,
    )

    latency = (
        time.perf_counter()
        - start
    )

    analytics_data[
        "total_auto_fix_latency_seconds"
    ] += latency

    if final_result["status"] == "success":

                analytics_data[
            "auto_fix_successes"
        ] += 1

        status = "success"

    else:

        analytics_data[
            "auto_fix_failures"
        ] += 1

        status = "failed"

    return {
        "status": status,
        "message": (
            "Maximum auto-fix cycles reached."
        ),
        "cycles": request.max_cycles,
        "files": [
            item.model_dump()
            for item in current_files
        ],
        "history": history,
        "execution": final_result,
        "fixed": (
            final_result["status"] == "success"
        ),
        "latency_seconds": round(
            latency,
            3,
        ),
    }


# ============================================================
# AUTO-FIX ENDPOINTS
# ============================================================

@app.post("/api/agent/autofix")
@app.post("/api/execute/autofix")
async def auto_fix(
    request: AutoFixRequest,
):

    start = time.perf_counter()

    try:

        result = await run_auto_fix_loop(
            request
        )

        record(
            start,
            result["status"] == "success",
        )

        return result

    except ValueError as exc:

        record(
            start,
            False,
        )

        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except Exception as exc:

        record(
            start,
            False,
        )

        logger.exception(
            "Auto-fix failed."
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Auto-fix failed",
                "message": str(exc),
            },
        )


# ============================================================
# BENCHMARK HELPERS
# ============================================================

def normalize_benchmark_output(
    text: str,
) -> str:

    return (
        text or ""
    ).strip().lower()


def evaluate_benchmark_case(
    case: BenchmarkCase,
    output: str,
) -> Dict[str, Any]:

    normalized = normalize_benchmark_output(
        output
    )

    found = []
    missed = []

    for issue in case.expected_issues:

        issue_normalized = (
            issue or ""
        ).strip().lower()

        if not issue_normalized:
            continue

        if issue_normalized in normalized:

            found.append(
                issue
            )

        else:

            missed.append(
                issue
            )

    total = len(
        case.expected_issues
    )

    if total > 0:

        score = round(
            (
                len(found)
                / total
            ) * 100,
            2,
        )

    else:

        score = (
            100.0
            if output.strip()
            else 0.0
        )

    return {
        "name": case.name,
        "expected_issues": case.expected_issues,
        "issues_found": found,
        "issues_missed": missed,
        "score_percent": score,
    }


# ============================================================
# BENCHMARK ENDPOINT
# ============================================================

@app.post("/api/benchmark/run")
async def run_benchmark(
    request: BenchmarkRequest,
):

    start = time.perf_counter()

    results = []

    try:

        for case in request.cases:

            analytics_data[
                "benchmark_runs"
            ] += 1

            analytics_data[
                "benchmark_cases"
            ] += 1

            prompt = (
                "You are Engineer AI Benchmark Agent.\n\n"
                f"LANGUAGE:\n{case.language}\n\n"
                "BROKEN CODE:\n"
                f"```{case.language}\n"
                f"{case.broken_code}\n"
                "```\n\n"
                "Identify the bugs and root causes. "
                "Provide corrected code and tests where useful. "
                "Check security and reliability. "
                "Do not claim execution."
            )

            output = await infer(
                prompt,
                AGENT_MAX_OUTPUT_TOKENS,
            )

            evaluation = evaluate_benchmark_case(
                case,
                output,
            )

            analytics_data[
                "benchmark_bugs_found"
            ] += len(
                evaluation[
                    "issues_found"
                ]
            )

            analytics_data[
                "benchmark_bugs_missed"
            ] += len(
                evaluation[
                    "issues_missed"
                ]
            )

            results.append({
                "case": case.name,
                "analysis": output,
                "evaluation": evaluation,
            })

        scores = [
            item[
                "evaluation"
            ][
                "score_percent"
            ]
            for item in results
        ]

        average_score = (
            round(
                sum(scores)
                / len(scores),
                2,
            )
            if scores
            else 0.0
        )

        record(
            start,
            True,
        )

        return {
            "status": "success",

            "results": results,

            "summary": {
                "cases": len(results),
                "average_score_percent":
                    average_score,
            },

            "tests_requested":
                request.run_tests,

            "tests_executed":
                False,

            "latency_seconds": round(
                time.perf_counter()
                - start,
                3,
            ),
        }

    except Exception as exc:

        record(
            start,
            False,
        )

        logger.exception(
            "Benchmark failed."
        )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "Benchmark failed",
                "message": str(exc),
            },
        )
# ============================================================
# ENGINEER AI BACKEND
# MILESTONE 3
# PART 5/5
# ============================================================


# ============================================================
# ANALYTICS
# ============================================================

@app.get("/api/ops/analytics")
async def analytics():

    total_requests = analytics_data[
        "total_requests"
    ]

    successful = analytics_data[
        "successful_responses"
    ]

    if total_requests > 0:

        success_rate = round(
            (
                successful
                / total_requests
            ) * 100,
            2,
        )

        average_latency = round(
            analytics_data[
                "total_latency_seconds"
            ]
            / total_requests,
            3,
        )

    else:

        success_rate = 0.0
        average_latency = 0.0

    execution_runs = analytics_data[
        "execution_runs"
    ]

    if execution_runs:

        execution_success_rate = round(
            (
                analytics_data[
                    "execution_successes"
                ]
                / execution_runs
            ) * 100,
            2,
        )

    else:

        execution_success_rate = 0.0

    test_runs = analytics_data[
        "test_runs"
    ]

    if test_runs:

        test_success_rate = round(
            (
                analytics_data[
                    "test_successes"
                ]
                / test_runs
            ) * 100,
            2,
        )

    else:

        test_success_rate = 0.0

    auto_fix_runs = analytics_data[
        "auto_fix_runs"
    ]

    if auto_fix_runs:

        auto_fix_success_rate = round(
            (
                analytics_data[
                    "auto_fix_successes"
                ]
                / auto_fix_runs
            ) * 100,
            2,
        )

    else:

        auto_fix_success_rate = 0.0

    return {

        # ====================================================
        # EXISTING ANALYTICS
        # ====================================================

        "total_requests":
            total_requests,

        "successful_responses":
            successful,

        "failed_requests":
            analytics_data[
                "failed_requests"
            ],

        "success_rate_percent":
            success_rate,

        "average_latency_seconds":
            average_latency,

        "agent_tasks_executed":
            analytics_data[
                "agent_tasks_executed"
            ],

        # ====================================================
        # PROJECT AGENTS
        # ====================================================

        "project_analysis_runs":
            analytics_data[
                "project_analysis_runs"
            ],

        "architecture_runs":
            analytics_data[
                "architecture_runs"
            ],

        "dependency_analysis_runs":
            analytics_data[
                "dependency_analysis_runs"
            ],

        "debugger_runs":
            analytics_data[
                "debugger_runs"
            ],

        "tests_generated":
            analytics_data[
                "tests_generated"
            ],

        "security_scans_completed":
            analytics_data[
                "security_scans_completed"
            ],

        "performance_analyses":
            analytics_data[
                "performance_analyses"
            ],

        # ====================================================
        # BENCHMARKS
        # ====================================================

        "benchmark_runs":
            analytics_data[
                "benchmark_runs"
            ],

        "benchmark_cases":
            analytics_data[
                "benchmark_cases"
            ],

        "benchmark_bugs_found":
            analytics_data[
                "benchmark_bugs_found"
            ],

        "benchmark_bugs_missed":
            analytics_data[
                "benchmark_bugs_missed"
            ],

        # ====================================================
        # MILESTONE 3 — EXECUTION
        # ====================================================

        "execution_runs":
            execution_runs,

        "execution_successes":
            analytics_data[
                "execution_successes"
            ],

        "execution_failures":
            analytics_data[
                "execution_failures"
            ],

        "execution_timeouts":
            analytics_data[
                "execution_timeouts"
            ],

        "execution_success_rate_percent":
            execution_success_rate,

        # ====================================================
        # TESTING
        # ====================================================

        "test_runs":
            test_runs,

        "test_successes":
            analytics_data[
                "test_successes"
            ],

        "test_failures":
            analytics_data[
                "test_failures"
            ],

        "test_success_rate_percent":
            test_success_rate,

        # ====================================================
        # AUTO-FIX
        # ====================================================

        "auto_fix_runs":
            auto_fix_runs,

        "auto_fix_successes":
            analytics_data[
                "auto_fix_successes"
            ],

        "auto_fix_failures":
            analytics_data[
                "auto_fix_failures"
            ],

        "auto_fix_success_rate_percent":
            auto_fix_success_rate,

        # ====================================================
        # EXECUTION SYSTEM
        # ====================================================

        "execution_mode":
            EXECUTION_MODE,

        "docker_available":
            bool(
                shutil.which("docker")
            ),

        "total_execution_latency_seconds":
            round(
                analytics_data[
                    "total_execution_latency_seconds"
                ],
                3,
            ),

        "total_test_latency_seconds":
            round(
                analytics_data[
                    "total_test_latency_seconds"
                ],
                3,
            ),

        "total_auto_fix_latency_seconds":
            round(
                analytics_data[
                    "total_auto_fix_latency_seconds"
                ],
                3,
            ),
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():

    logger.info(
        "%s v%s starting",
        APP_TITLE,
        APP_VERSION,
    )

    logger.info(
        "AI provider: %s",
        DEFAULT_PROVIDER,
    )

    logger.info(
        "AI model: %s",
        DEFAULT_MODEL,
    )

    logger.info(
        "Execution mode: %s",
        EXECUTION_MODE,
    )

    if EXECUTION_MODE == "docker":

        if shutil.which("docker"):

            logger.info(
                "Docker sandbox detected."
            )

        else:

            logger.warning(
                "Docker is NOT available. "
                "Execution endpoints will be unavailable "
                "until a sandbox is provided."
            )


# ============================================================
# SHUTDOWN
# ============================================================

@app.on_event("shutdown")
async def shutdown_event():

    logger.info(
        "%s shutting down",
        APP_TITLE,
    )


# ============================================================
# LOCAL SERVER
# ============================================================

if __name__ == "__main__":

    import uvicorn

    host = os.getenv(
        "HOST",
        "0.0.0.0",
    )

    port = int(
        os.getenv(
            "PORT",
            "8000",
        )
    )

    uvicorn.run(
        app,
        host=host,
        port=port,
    )
    