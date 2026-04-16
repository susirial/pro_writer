import json
import re
import asyncio
import hashlib
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional

from veadk import Runner
from veadk.memory.short_term_memory import ShortTermMemory
from core.config import config, BASE_DIR
from core.logger import logger
from core.agent_factory import AgentFactory
from core.reviewer_pool import reviewer_pool
from core.memory import memory_manager
from tools.robust_file_tools import read_file, write_file

class FSMState(str, Enum):
    INIT = "Init"
    WAIT_USER = "Wait_User"
    PLAN = "Plan"
    CONFIRM_PLAN = "Confirm_Plan"
    WRITE = "Write"
    ASYNC_REVIEW = "Async_Review"
    DECIDE = "Decide"
    REWRITE = "Rewrite"
    NEXT_CHAPTER = "Next_Chapter"
    DONE = "Done"

class NovelOrchestrator:
    def __init__(self):
        self.state = FSMState.INIT
        self.current_chapter = 1
        self.total_chapters = 1  # 默认值，在Plan阶段更新
        self.retry_count = 0     # 用于重试同一状态
        self.rewrite_count = 0   # 用于控制重写次数
        self.last_command: Optional[str] = None
        self.requirements_confirmed = False
        self.plan_confirmed = False
        self.halt_reason: Optional[str] = None
        self.force_rewrite_pending = False
        
        self.max_retries = config.get("system.max_retries", 3)
        self.max_rewrites = config.get("system.max_rewrites", 3)
        
        self.writer = AgentFactory.create_writer()
        self.reviewer_pool = reviewer_pool
        self.app_name = config.get("system.app_name", "novel_agent_system")
        self.output_dir = config.get("system.output_dir", "output")
        self.short_term_memory = ShortTermMemory()
        
        self.status_file = f"{self.output_dir}/写作任务状态.md"
        self.requirements_file = f"{self.output_dir}/用户需求.md"
        self.plan_file = f"{self.output_dir}/写作计划.md"
        self.plan_confirmation_file = f"{self.output_dir}/写作计划确认.md"

    def _chapter_file(self, chapter: Optional[int] = None) -> str:
        n = chapter or self.current_chapter
        return f"{self.output_dir}/contents/chapter{n}/第{n}章.md"

    def _read_text_or_empty(self, file_path: str) -> str:
        try:
            return read_file(file_path)
        except Exception:
            return ""

    def _extract_plan_base_setting(self, plan: str) -> str:
        m = re.search(r"(##\s*一、基础设定[\s\S]*?)(?=\n##\s*二、整体结构|\Z)", plan)
        return (m.group(1).strip() if m else "").strip()

    def _extract_plan_arc_row(self, plan: str, episode: int) -> str:
        for line in plan.splitlines():
            if "| Arc" not in line:
                continue
            m = re.search(r"\|\s*Arc\d+\s*\|.*?\|\s*(\d+)\s*-\s*(\d+)\s*\|", line)
            if not m:
                continue
            start = int(m.group(1))
            end = int(m.group(2))
            if start <= episode <= end:
                return line.strip()
        return ""

    def _extract_total_chapters(self, requirements: str) -> int:
        patterns = [
            r"章节数量（可选）\s*[:：]\s*(\d+)",
            r"章节数量\s*[:：]\s*(\d+)",
            r"共\s*(\d+)\s*集",
            r"(\d+)\s*集",
            r"(\d+)\s*章",
        ]
        for pattern in patterns:
            m = re.search(pattern, requirements)
            if m:
                try:
                    value = int(m.group(1))
                    if value > 0:
                        return value
                except Exception:
                    continue
        return config.get("system.default_total_chapters", 3)

    def _is_confirmed(self, file_path: str, keyword: str) -> bool:
        try:
            content = read_file(file_path)
        except Exception:
            return False

        for line in content.splitlines():
            if keyword in line and re.search(r"^\s*[-*]\s*\[\s*x\s*\]", line, re.IGNORECASE):
                return True
        return False

    def _refresh_confirm_flags(self):
        self.requirements_confirmed = self._is_confirmed(self.requirements_file, "我已确认")
        self.plan_confirmed = self._is_confirmed(self.plan_confirmation_file, "我已确认")

    def _write_requirements_template_if_missing(self):
        target = Path(self.requirements_file)
        if not target.is_absolute():
            target = BASE_DIR / target
        if target.exists():
            return
        template = (
            "# 用户需求\n\n"
            "请在下方补充本次小说创作的需求信息，并在确认无误后勾选确认项。\n\n"
            "## 需求内容\n"
            "- 题材/类型：\n"
            "- 主题：\n"
            "- 目标读者：\n"
            "- 世界观/背景设定：\n"
            "- 主角设定：\n"
            "- 关键冲突：\n"
            "- 风格与禁忌：\n"
            "- 章节数量（可选）：\n\n"
            "## 确认\n"
            "- [ ] 我已确认以上需求无误（将 [ ] 改为 [x]）\n"
        )
        write_file(self.requirements_file, template)

    def _write_plan_confirmation_template_if_missing(self):
        target = Path(self.plan_confirmation_file)
        if not target.is_absolute():
            target = BASE_DIR / target
        if target.exists():
            return
        template = (
            "# 写作计划确认\n\n"
            "请阅读 output/写作计划.md 的内容，确认无误后勾选确认项。\n\n"
            "## 确认\n"
            "- [ ] 我已确认写作计划无误（将 [ ] 改为 [x]）\n"
        )
        write_file(self.plan_confirmation_file, template)
        
    def _save_state(self):
        self._refresh_confirm_flags()
        state_data = {
            "state": self.state.value,
            "current_chapter": self.current_chapter,
            "total_chapters": self.total_chapters,
            "retry_count": self.retry_count,
            "rewrite_count": self.rewrite_count,
            "last_command": self.last_command,
            "requirements_confirmed": self.requirements_confirmed,
            "plan_confirmed": self.plan_confirmed,
            "halt_reason": self.halt_reason,
            "force_rewrite_pending": self.force_rewrite_pending,
        }
        content = f"# 写作任务状态\n\n```json\n{json.dumps(state_data, indent=4, ensure_ascii=False)}\n```\n"
        write_file(self.status_file, content)
        logger.info(f"保存状态: {state_data}")

    def _load_state(self) -> bool:
        try:
            content = read_file(self.status_file)
            match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
                state_value = data.get("state", "Init")
                try:
                    self.state = FSMState(state_value)
                except Exception:
                    self.state = FSMState.INIT
                self.current_chapter = data.get("current_chapter", 1)
                self.total_chapters = data.get("total_chapters", 1)
                self.retry_count = data.get("retry_count", 0)
                self.rewrite_count = data.get("rewrite_count", 0)
                self.last_command = data.get("last_command")
                self.requirements_confirmed = data.get("requirements_confirmed", False)
                self.plan_confirmed = data.get("plan_confirmed", False)
                self.halt_reason = data.get("halt_reason")
                self.force_rewrite_pending = data.get("force_rewrite_pending", False)
                self._refresh_confirm_flags()
                logger.info(f"恢复状态: {data}")
                return True
        except Exception as e:
            logger.warning(f"无法读取或解析状态文件: {e}")
        return False

    async def _run_agent(self, agent, prompt: str) -> str:
        runner = Runner(
            agent=agent,
            short_term_memory=self.short_term_memory,
            app_name=self.app_name,
            user_id="orchestrator",
        )
        response = await runner.run(prompt, session_id=f"chapter_{self.current_chapter}")
        return response

    async def handle_init(self):
        logger.info("FSM: Init")
        self._write_requirements_template_if_missing()
        if self._load_state():
            if self.state == FSMState.INIT:
                self._refresh_confirm_flags()
                if not self.requirements_confirmed:
                    self.state = FSMState.WAIT_USER
                    self.halt_reason = f"等待用户填写并确认 {self.requirements_file}"
                    self._save_state()
                    return
                self.state = FSMState.PLAN
                self.halt_reason = None
                self._save_state()
        else:
            self._refresh_confirm_flags()
            if not self.requirements_confirmed:
                self.state = FSMState.WAIT_USER
                self.halt_reason = f"等待用户填写并确认 {self.requirements_file}"
            else:
                self.state = FSMState.PLAN
            self._save_state()

    async def handle_plan(self):
        logger.info("FSM: Plan")
        self._write_requirements_template_if_missing()
        self._refresh_confirm_flags()
        if not self.requirements_confirmed:
            self.state = FSMState.WAIT_USER
            self.halt_reason = f"缺少已确认的用户需求文件：{self.requirements_file}"
            self._save_state()
            return

        requirements = read_file(self.requirements_file)
        total_chapters = self._extract_total_chapters(requirements)

        prompt = (
            "你是作家智能体，请严格根据【用户需求】生成【写作计划.md】内容。\n"
            "要求：\n"
            "1) 产出必须与用户需求一致，不允许自行改题材/类型/主角/核心设定。\n"
            "2) 计划结构请包含：基础设定、整体结构（弧/卷/章节划分）、关键人物表、核心冲突线、爽点节奏安排、伏笔与回收清单、每章/每集推进目标。\n"
            f"3) 章节/集数规模以用户需求为准；若需求未明确，则使用总计 {total_chapters} 章。\n"
            "4) 直接输出 Markdown 正文，不要提及工具调用，不要输出多余解释。\n\n"
            f"【用户需求】\n{requirements}\n"
        )

        plan_markdown = await self._run_agent(self.writer, prompt)
        write_file(self.plan_file, plan_markdown)

        self.total_chapters = total_chapters
        self._write_plan_confirmation_template_if_missing()
        self.state = FSMState.CONFIRM_PLAN
        self.retry_count = 0
        self.rewrite_count = 0
        self.halt_reason = f"写作计划已生成，等待确认：{self.plan_confirmation_file}"
        self._save_state()

    async def handle_write(self):
        logger.info(f"FSM: Write (Chapter {self.current_chapter})")

        requirements = self._read_text_or_empty(self.requirements_file)
        plan = self._read_text_or_empty(self.plan_file)
        plan_base = self._extract_plan_base_setting(plan)
        arc_row = self._extract_plan_arc_row(plan, self.current_chapter)

        prompt = (
            "你是作家智能体。请严格依据【用户需求】与【写作计划节选】撰写当前章节正文。\n"
            "硬性要求：\n"
            "1) 禁止更换题材/主角/世界观/能力体系；不得写成赘婿、总裁、都市修仙等无关类型。\n"
            "2) 当前章节编号视作“第 N 集/章”，必须写在标题中。\n"
            "3) 只输出 Markdown 正文（包含标题与正文），不要调用任何工具，不要输出额外解释。\n"
            "4) 字数目标 3000～4000 字，节奏符合起点男频快节奏爽文最佳实践。\n\n"
            f"【当前章节】第 {self.current_chapter} 集/章\n\n"
            f"【用户需求】\n{requirements}\n\n"
            f"【写作计划节选-基础设定】\n{plan_base}\n\n"
            f"【写作计划节选-当前弧信息（若为空请依据整体结构自行定位，不得脱离题材）】\n{arc_row}\n"
        )

        chapter_markdown = await self._run_agent(self.writer, prompt)
        write_file(self._chapter_file(), chapter_markdown)
        
        self.state = FSMState.ASYNC_REVIEW
        self.retry_count = 0
        self._save_state()

    async def handle_async_review(self):
        logger.info(f"FSM: Async_Review (Chapter {self.current_chapter})")
        chapter_file = self._chapter_file()
        try:
            content = read_file(chapter_file)
        except Exception as e:
            logger.warning(f"章节文件不存在或不可读，回退到 WRITE: {e}")
            self.state = FSMState.WRITE
            self.halt_reason = f"章节文件缺失，已回退到写作阶段：{chapter_file}"
            self._save_state()
            return

        logger.info(f"[review] 开始并发评审: chapter={self.current_chapter} reviewers=1 readers=5")
        results = await self.reviewer_pool.run_concurrent_reviews(content, f"chapter_{self.current_chapter}")
        logger.info(f"[review] 并发评审结束: chapter={self.current_chapter} errors={len(results.get('errors') or [])}")
        
        # 保存读者反馈
        logger.info(f"[review] 写入评审反馈文件: chapter={self.current_chapter}")
        feedback_content = f"# 第 {self.current_chapter} 章评审反馈\n\n"
        if results.get("reviewer"):
            feedback_content += f"## 审阅专员反馈\n{results['reviewer']}\n\n"
        for reader in results.get("readers", []):
            feedback_content += f"## {reader['agent']} 反馈\n{reader['feedback']}\n\n"
        if results.get("errors"):
            feedback_content += f"## 错误信息\n{results['errors']}\n\n"
            
        feedback_file = f"{self.output_dir}/contents/chapter{self.current_chapter}/第{self.current_chapter}章读者反馈.md"
        write_file(feedback_file, feedback_content)
        
        self.force_rewrite_pending = True
        logger.info(f"[rewrite] 强制修订已触发: chapter={self.current_chapter}")
        self.state = FSMState.REWRITE
        self.retry_count = 0
        self._save_state()

    async def handle_decide(self):
        logger.info(f"FSM: Decide (Chapter {self.current_chapter})")
        feedback_file = f"{self.output_dir}/contents/chapter{self.current_chapter}/第{self.current_chapter}章读者反馈.md"
        feedback = read_file(feedback_file)
        
        # 使用审阅专员或Orchestrator自身来做决定
        orchestrator_agent = AgentFactory.create_orchestrator([])
        prompt = (
            f"请阅读以下针对第 {self.current_chapter} 章的评审反馈，并决定该章节是否通过审核。\n"
            f"如果大部分反馈是正面的且没有严重问题，至少需要修复1次用户返回的主要问题，之后请在回答的开头明确包含【通过】二字；\n"
            f"如果问题严重需要重写，请在回答的开头明确包含【不通过】二字，并给出重写建议。\n\n"
            f"反馈内容：\n{feedback}"
        )
        response = await self._run_agent(orchestrator_agent, prompt)
        
        decision_file = f"{self.output_dir}/contents/chapter{self.current_chapter}/第{self.current_chapter}章是否通过审核.md"
        write_file(decision_file, response)
        
        if "【通过】" in response or self.rewrite_count >= self.max_rewrites:
            if self.rewrite_count >= self.max_rewrites:
                logger.warning(f"达到最大重写次数 ({self.max_rewrites})，强制通过审核兜底。")
            self.state = FSMState.NEXT_CHAPTER
        else:
            self.rewrite_count += 1
            self.state = FSMState.REWRITE
            
        self.retry_count = 0
        self._save_state()

    async def handle_rewrite(self):
        logger.info(f"FSM: Rewrite (Chapter {self.current_chapter}, Rewrite count: {self.rewrite_count})")
        feedback_file = f"{self.output_dir}/contents/chapter{self.current_chapter}/第{self.current_chapter}章读者反馈.md"
        decision_file = f"{self.output_dir}/contents/chapter{self.current_chapter}/第{self.current_chapter}章是否通过审核.md"
        
        feedback = read_file(feedback_file)
        feedback_digest = hashlib.sha256(feedback.encode("utf-8")).hexdigest()[:12]
        logger.info(
            f"[rewrite] 使用评审反馈: chapter={self.current_chapter} file={feedback_file} chars={len(feedback)} sha256={feedback_digest}"
        )

        requirements = self._read_text_or_empty(self.requirements_file)
        plan = self._read_text_or_empty(self.plan_file)
        plan_base = self._extract_plan_base_setting(plan)
        arc_row = self._extract_plan_arc_row(plan, self.current_chapter)

        decision = ""
        if not self.force_rewrite_pending:
            decision = self._read_text_or_empty(decision_file)
            if decision:
                decision_digest = hashlib.sha256(decision.encode("utf-8")).hexdigest()[:12]
                logger.info(
                    f"[rewrite] 使用审核决策建议: chapter={self.current_chapter} file={decision_file} chars={len(decision)} sha256={decision_digest}"
                )
        
        if self.force_rewrite_pending:
            logger.info(f"[rewrite] 进入强制修订: chapter={self.current_chapter} reason=post_review")
            prompt = (
                "你是作家智能体。当前章节已完成评审，系统要求你必须进行一次强制修订，然后才能进入下一章节。\n"
                "硬性要求：\n"
                "1) 保持题材/主角/世界观/能力体系与写作计划一致，不得改写为其他类型。\n"
                "2) 保持本章核心剧情不变，重点修复评审中指出的主要问题：逻辑、人物、节奏、语言AI味、画面等。\n"
                "3) 直接输出修订后的 Markdown 正文（包含标题与正文），不要调用任何工具，不要输出额外解释。\n"
                "4) 字数目标 3000～4000 字。\n\n"
                f"【当前章节】第 {self.current_chapter} 集/章\n\n"
                f"【用户需求】\n{requirements}\n\n"
                f"【写作计划节选-基础设定】\n{plan_base}\n\n"
                f"【写作计划节选-当前弧信息】\n{arc_row}\n\n"
                f"【评审反馈】\n{feedback}\n"
            )
        else:
            logger.info(f"[rewrite] 进入重写: chapter={self.current_chapter} reason=not_passed")
            prompt = (
                f"第 {self.current_chapter} 章未能通过评审。请根据以下反馈和建议对章节进行重写。\n"
                "硬性要求：\n"
                "1) 保持题材/主角/世界观/能力体系与写作计划一致，不得改写为其他类型。\n"
                "2) 直接输出 Markdown 正文（包含标题与正文），不要调用任何工具，不要输出额外解释。\n"
                "3) 字数目标 3000～4000 字。\n\n"
                f"【当前章节】第 {self.current_chapter} 集/章\n\n"
                f"【用户需求】\n{requirements}\n\n"
                f"【写作计划节选-基础设定】\n{plan_base}\n\n"
                f"【写作计划节选-当前弧信息】\n{arc_row}\n\n"
                f"【评审反馈】\n{feedback}\n\n"
                f"【重写建议】\n{decision}\n"
            )

        injected = ("【评审反馈】" in prompt) and (feedback in prompt)
        logger.info(
            f"[rewrite] 重写提示词已注入评审反馈: chapter={self.current_chapter} injected={injected} feedback_sha256={feedback_digest} prompt_chars={len(prompt)}"
        )
        logger.info(f"[rewrite] 调用 writer 开始生成修订稿: chapter={self.current_chapter}")
        chapter_markdown = await self._run_agent(self.writer, prompt)
        logger.info(f"[rewrite] writer 生成完成，写回章节文件: chapter={self.current_chapter}")
        write_file(self._chapter_file(), chapter_markdown)
        
        if self.force_rewrite_pending:
            self.force_rewrite_pending = False
            logger.info(f"[rewrite] 强制修订完成，推进到下一章: chapter={self.current_chapter}")
            self.state = FSMState.NEXT_CHAPTER
        else:
            self.state = FSMState.ASYNC_REVIEW
        self.retry_count = 0
        self._save_state()

    async def handle_next_chapter(self):
        logger.info(f"FSM: Next_Chapter (Chapter {self.current_chapter})")
        
        # Task 3.2 章节切换时记忆自动提炼
        chapter_file = f"{self.output_dir}/contents/chapter{self.current_chapter}/第{self.current_chapter}章.md"
        try:
            content = read_file(chapter_file)
            summary_prompt = f"请将以下章节内容提炼为一段简短的剧情摘要：\n{content}"
            # 用 orchestrator_agent 生成摘要
            orchestrator_agent = AgentFactory.create_orchestrator([])
            summary = await self._run_agent(orchestrator_agent, summary_prompt)
            
            # 为writer保存摘要并清空短期记忆
            memory_manager.convert_to_summary(self.writer.name, f"第 {self.current_chapter} 章: {summary}")
        except Exception as e:
            logger.error(f"提取章节摘要失败: {e}", exc_info=True)
            
        if self.current_chapter >= self.total_chapters:
            self.state = FSMState.DONE
            logger.info("所有章节已完成，小说创作工作流结束。")
        else:
            self.current_chapter += 1
            self.state = FSMState.WRITE
            self.rewrite_count = 0
            
        self.retry_count = 0
        self._save_state()

    async def run(self):
        logger.info("开始执行小说创作与评审工作流 FSM...")
        while self.state != FSMState.DONE:
            try:
                if self.state == FSMState.INIT:
                    await self.handle_init()
                elif self.state == FSMState.WAIT_USER:
                    self._write_requirements_template_if_missing()
                    self._refresh_confirm_flags()
                    if not self.requirements_confirmed:
                        self.halt_reason = f"等待用户填写并确认 {self.requirements_file}"
                        self._save_state()
                        return
                    self.state = FSMState.PLAN
                    self.halt_reason = None
                    self._save_state()
                elif self.state == FSMState.PLAN:
                    await self.handle_plan()
                    if self.state == FSMState.WAIT_USER:
                        return
                elif self.state == FSMState.CONFIRM_PLAN:
                    self._write_plan_confirmation_template_if_missing()
                    self._refresh_confirm_flags()
                    if not self.plan_confirmed:
                        self.halt_reason = f"等待确认写作计划：{self.plan_confirmation_file}"
                        self._save_state()
                        return
                    self.state = FSMState.WRITE
                    self.halt_reason = None
                    self._save_state()
                elif self.state == FSMState.WRITE:
                    await self.handle_write()
                elif self.state == FSMState.ASYNC_REVIEW:
                    await self.handle_async_review()
                elif self.state == FSMState.DECIDE:
                    await self.handle_decide()
                elif self.state == FSMState.REWRITE:
                    await self.handle_rewrite()
                elif self.state == FSMState.NEXT_CHAPTER:
                    await self.handle_next_chapter()
                else:
                    logger.error(f"未知状态: {self.state}")
                    break
            except Exception as e:
                logger.error(f"在状态 {self.state} 发生错误: {e}", exc_info=True)
                self.retry_count += 1
                if self.retry_count > self.max_retries:
                    logger.error(f"状态 {self.state} 连续重试 {self.max_retries} 次失败，中止工作流兜底。")
                    break
                logger.info(f"等待 2 秒后重试状态 {self.state} (尝试 {self.retry_count}/{self.max_retries})...")
                await asyncio.sleep(2)
                
        logger.info("FSM 工作流执行完毕。")
