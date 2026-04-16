import asyncio
import argparse
import sys

from typing import Optional

from tools.robust_file_tools import read_file

_logger = None

def get_logger():
    global _logger
    if _logger is None:
        from core.logger import setup_logger
        _logger = setup_logger()
    return _logger

def get_orchestrator_and_state():
    from core.orchestrator import NovelOrchestrator, FSMState
    return NovelOrchestrator, FSMState

EXIT_OK = 0
EXIT_INVALID_ARGS = 2
EXIT_GATED = 3
EXIT_RUNTIME_ERROR = 10

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel_orchestrator.py")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="只初始化输出目录与状态文件，不调用大模型")
    subparsers.add_parser("plan", help="生成写作计划并进入确认点（需要先确认用户需求）")
    run_parser = subparsers.add_parser("run", help="在确认写作计划后进入写作流程")
    run_parser.add_argument("--show-plan-lines", type=int, default=40, help="运行前打印写作计划前 N 行用于调试（默认 40）")
    run_parser.add_argument("--no-show-plan", action="store_true", help="关闭运行前写作计划摘要打印")

    review_parser = subparsers.add_parser("review", help="仅评审指定章节，不写新内容")
    review_parser.add_argument("--chapter", type=int, default=None, help="指定章节号（默认使用状态文件中的 current_chapter）")

    subparsers.add_parser("resume", help="从状态文件断点续传（若处于确认点则停住并提示）")
    return parser

async def cmd_init() -> int:
    NovelOrchestrator, FSMState = get_orchestrator_and_state()
    orchestrator = NovelOrchestrator()
    orchestrator.last_command = "init"
    orchestrator._write_requirements_template_if_missing()
    orchestrator.state = FSMState.WAIT_USER
    orchestrator.halt_reason = f"等待用户填写并确认 {orchestrator.requirements_file}"
    orchestrator._save_state()
    get_logger().info(orchestrator.halt_reason)
    return EXIT_OK

async def cmd_plan() -> int:
    NovelOrchestrator, FSMState = get_orchestrator_and_state()
    orchestrator = NovelOrchestrator()
    orchestrator._write_requirements_template_if_missing()
    orchestrator._load_state()
    orchestrator.last_command = "plan"
    orchestrator._refresh_confirm_flags()
    if not orchestrator.requirements_confirmed:
        orchestrator.state = FSMState.WAIT_USER
        orchestrator.halt_reason = f"缺少已确认的用户需求文件：{orchestrator.requirements_file}"
        orchestrator._save_state()
        get_logger().warning(orchestrator.halt_reason)
        print(orchestrator.halt_reason, file=sys.stderr)
        return EXIT_GATED

    orchestrator.state = FSMState.PLAN
    orchestrator.halt_reason = None
    await orchestrator.handle_plan()
    get_logger().info(f"写作计划已生成，等待确认：{orchestrator.plan_confirmation_file}")
    return EXIT_OK

def _print_plan_excerpt(plan_file: str, lines: int) -> None:
    if lines <= 0:
        return
    try:
        content = read_file(plan_file)
    except Exception as e:
        get_logger().warning(f"无法读取写作计划文件用于调试输出: {e}")
        return

    excerpt = "\n".join(content.splitlines()[:lines]).rstrip()
    if not excerpt:
        get_logger().warning("写作计划文件为空，跳过调试输出")
        return

    print("\n========== 写作计划（节选） ==========", file=sys.stderr)
    print(f"文件: {plan_file}", file=sys.stderr)
    print(excerpt, file=sys.stderr)
    print("========== 写作计划（节选结束） ==========\n", file=sys.stderr)

async def cmd_run(show_plan_lines: int, no_show_plan: bool) -> int:
    NovelOrchestrator, FSMState = get_orchestrator_and_state()
    orchestrator = NovelOrchestrator()
    orchestrator._write_requirements_template_if_missing()
    orchestrator._write_plan_confirmation_template_if_missing()
    orchestrator._load_state()
    orchestrator.last_command = "run"
    orchestrator._refresh_confirm_flags()

    if not orchestrator.requirements_confirmed:
        orchestrator.state = FSMState.WAIT_USER
        orchestrator.halt_reason = f"缺少已确认的用户需求文件：{orchestrator.requirements_file}"
        orchestrator._save_state()
        get_logger().warning(orchestrator.halt_reason)
        print(orchestrator.halt_reason, file=sys.stderr)
        return EXIT_GATED

    if not orchestrator.plan_confirmed:
        orchestrator.state = FSMState.CONFIRM_PLAN
        orchestrator.halt_reason = f"缺少已确认的写作计划确认文件：{orchestrator.plan_confirmation_file}"
        orchestrator._save_state()
        get_logger().warning(orchestrator.halt_reason)
        print(orchestrator.halt_reason, file=sys.stderr)
        return EXIT_GATED

    if orchestrator.state == FSMState.CONFIRM_PLAN:
        orchestrator.state = FSMState.WRITE
        orchestrator.halt_reason = None
        orchestrator._save_state()

    if orchestrator.state in (FSMState.INIT, FSMState.WAIT_USER, FSMState.PLAN):
        orchestrator.state = FSMState.WRITE
        orchestrator.halt_reason = None
        orchestrator._save_state()

    if not no_show_plan:
        _print_plan_excerpt(orchestrator.plan_file, show_plan_lines)

    await orchestrator.run()
    return EXIT_OK

async def cmd_review(chapter: Optional[int]) -> int:
    NovelOrchestrator, FSMState = get_orchestrator_and_state()
    orchestrator = NovelOrchestrator()
    if not orchestrator._load_state():
        msg = f"找不到状态文件：{orchestrator.status_file}，请先执行 init/plan/run"
        get_logger().error(msg)
        print(msg, file=sys.stderr)
        return EXIT_GATED
    orchestrator.last_command = "review"

    if chapter is None:
        chapter = orchestrator.current_chapter
    if chapter <= 0:
        msg = "--chapter 必须为正整数"
        get_logger().error(msg)
        print(msg, file=sys.stderr)
        return EXIT_INVALID_ARGS
    if orchestrator.total_chapters and chapter > orchestrator.total_chapters:
        msg = f"--chapter 超出总章节数（{chapter} > {orchestrator.total_chapters}）"
        get_logger().error(msg)
        print(msg, file=sys.stderr)
        return EXIT_INVALID_ARGS

    orchestrator.current_chapter = chapter
    orchestrator.state = FSMState.ASYNC_REVIEW
    orchestrator.halt_reason = None
    orchestrator._save_state()
    await orchestrator.handle_async_review()
    await orchestrator.handle_decide()
    return EXIT_OK

async def cmd_resume() -> int:
    NovelOrchestrator, FSMState = get_orchestrator_and_state()
    orchestrator = NovelOrchestrator()
    orchestrator._write_requirements_template_if_missing()
    orchestrator._write_plan_confirmation_template_if_missing()
    if not orchestrator._load_state():
        orchestrator.state = FSMState.WAIT_USER
        orchestrator.halt_reason = f"未找到状态文件，已初始化并等待用户确认：{orchestrator.requirements_file}"
        orchestrator._save_state()
        get_logger().warning(orchestrator.halt_reason)
        print(orchestrator.halt_reason, file=sys.stderr)
        return EXIT_GATED
    orchestrator.last_command = "resume"

    if orchestrator.state in (FSMState.WAIT_USER, FSMState.CONFIRM_PLAN):
        orchestrator._refresh_confirm_flags()
        if orchestrator.state == FSMState.WAIT_USER:
            msg = f"当前停在确认点 WAIT_USER，请先填写并确认：{orchestrator.requirements_file}，再执行 plan"
        else:
            msg = f"当前停在确认点 CONFIRM_PLAN，请先确认：{orchestrator.plan_confirmation_file}，再执行 run"
        orchestrator.halt_reason = msg
        orchestrator._save_state()
        get_logger().warning(msg)
        print(msg, file=sys.stderr)
        return EXIT_GATED

    orchestrator._refresh_confirm_flags()
    if not orchestrator.requirements_confirmed and orchestrator.state in (FSMState.INIT, FSMState.PLAN):
        orchestrator.state = FSMState.WAIT_USER
        orchestrator.halt_reason = f"缺少已确认的用户需求文件：{orchestrator.requirements_file}"
        orchestrator._save_state()
        get_logger().warning(orchestrator.halt_reason)
        print(orchestrator.halt_reason, file=sys.stderr)
        return EXIT_GATED

    if orchestrator.state in (
        FSMState.WRITE,
        FSMState.ASYNC_REVIEW,
        FSMState.DECIDE,
        FSMState.REWRITE,
        FSMState.NEXT_CHAPTER,
    ) and not orchestrator.plan_confirmed:
        orchestrator.state = FSMState.CONFIRM_PLAN
        orchestrator.halt_reason = f"缺少已确认的写作计划确认文件：{orchestrator.plan_confirmation_file}"
        orchestrator._save_state()
        get_logger().warning(orchestrator.halt_reason)
        print(orchestrator.halt_reason, file=sys.stderr)
        return EXIT_GATED

    await orchestrator.run()
    return EXIT_OK

if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        raise SystemExit(EXIT_OK)

    try:
        if args.command == "init":
            raise SystemExit(asyncio.run(cmd_init()))
        if args.command == "plan":
            raise SystemExit(asyncio.run(cmd_plan()))
        if args.command == "run":
            raise SystemExit(asyncio.run(cmd_run(args.show_plan_lines, args.no_show_plan)))
        if args.command == "review":
            raise SystemExit(asyncio.run(cmd_review(args.chapter)))
        if args.command == "resume":
            raise SystemExit(asyncio.run(cmd_resume()))
        parser.print_help()
        raise SystemExit(EXIT_INVALID_ARGS)
    except SystemExit:
        raise
    except Exception as e:
        get_logger().error(f"运行失败: {e}", exc_info=True)
        print(f"运行失败: {e}", file=sys.stderr)
        raise SystemExit(EXIT_RUNTIME_ERROR)
