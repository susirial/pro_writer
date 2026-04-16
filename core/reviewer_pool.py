import asyncio
from typing import List, Dict, Any
from veadk import Runner
from veadk.memory.short_term_memory import ShortTermMemory

from core.agent_factory import AgentFactory
from core.logger import logger
from core.config import config

class ReviewerPool:
    """
    异步并发评审池：并发调用 1 个审阅专员与 5 个读者智能体。
    """
    def __init__(self):
        self.app_name = config.get("system.app_name", "novel_agent_system")
        self.short_term_memory = ShortTermMemory()
        self.reviewer = AgentFactory.create_reviewer()
        self.readers = AgentFactory.create_readers()

    async def _run_single_agent(self, agent, content: str, session_id: str) -> Dict[str, Any]:
        """
        运行单个智能体进行评审。
        这里捕获异常，确保单个任务失败不会导致整个并发崩溃。
        """
        try:
            logger.info(f"[{agent.name}] 开始评审...")
            runner = Runner(
                agent=agent,
                short_term_memory=self.short_term_memory,
                app_name=self.app_name,
                user_id="system_reviewer",
            )
            
            # 如果需要结合短期记忆模块，可以在这里通过 agent.memory_manager 获取上下文
            # 此处简单以 content 作为输入进行评审
            prompt = f"请根据你的设定，对以下内容进行评审：\n\n{content}"
            
            response = await runner.run(
                messages=prompt,
                session_id=session_id
            )
            
            logger.info(f"[{agent.name}] 评审完成。")
            
            # 将评审结果保存到记忆模块中
            agent.memory_manager.add_memory(agent.name, "user", prompt)
            agent.memory_manager.add_memory(agent.name, "assistant", str(response))
            
            return {
                "agent_name": agent.name,
                "status": "success",
                "result": response
            }
        except Exception as e:
            logger.error(f"[{agent.name}] 评审过程中发生异常: {e}", exc_info=True)
            return {
                "agent_name": agent.name,
                "status": "error",
                "error": str(e)
            }

    async def run_concurrent_reviews(self, chapter_content: str, session_id: str) -> Dict[str, Any]:
        """
        并发执行 6 个评审任务并聚合结果。
        """
        logger.info("启动并发评审流程...")
        tasks = []
        
        # 1. 添加审阅专员任务
        tasks.append(self._run_single_agent(self.reviewer, chapter_content, session_id))
        
        # 2. 添加 5 个读者智能体任务
        for reader in self.readers:
            tasks.append(self._run_single_agent(reader, chapter_content, session_id))
            
        # 并发执行并等待结果
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 聚合数据
        aggregated = {
            "reviewer": None,
            "readers": [],
            "errors": []
        }
        
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"并发任务执行抛出未捕获异常: {res}", exc_info=res)
                aggregated["errors"].append(str(res))
                continue
                
            if res["status"] == "error":
                aggregated["errors"].append({
                    "agent": res["agent_name"],
                    "error": res["error"]
                })
            elif res["agent_name"] == "reviewer":
                aggregated["reviewer"] = res["result"]
            else:
                aggregated["readers"].append({
                    "agent": res["agent_name"],
                    "feedback": res["result"]
                })
                
        logger.info("并发评审流程结束，结果已聚合。")
        return aggregated

# 全局单例
reviewer_pool = ReviewerPool()
