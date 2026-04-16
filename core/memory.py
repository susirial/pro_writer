import json
from typing import List, Dict, Any, Optional
from core.logger import setup_logger

logger = setup_logger()

class MemoryManager:
    """
    短期记忆管理模块，支持为单个智能体附加/清理上下文记忆，
    并在章节切换时支持将明细记忆提炼为摘要并释放旧记忆。
    """
    def __init__(self):
        # agent_id -> list of memory dicts: [{"role": "...", "content": "..."}]
        self._short_term_memories: Dict[str, List[Dict[str, str]]] = {}
        # agent_id -> list of chapter summaries
        self._chapter_summaries: Dict[str, List[str]] = {}

    def _init_agent(self, agent_id: str):
        if agent_id not in self._short_term_memories:
            self._short_term_memories[agent_id] = []
        if agent_id not in self._chapter_summaries:
            self._chapter_summaries[agent_id] = []

    def add_memory(self, agent_id: str, role: str, content: str):
        """
        为单个智能体附加一条上下文记忆。
        :param agent_id: 智能体唯一标识
        :param role: 角色，如 'user', 'assistant', 'system'
        :param content: 记忆内容
        """
        self._init_agent(agent_id)
        self._short_term_memories[agent_id].append({"role": role, "content": content})
        logger.debug(f"Added memory to agent '{agent_id}' (role: {role}).")

    def get_short_term_memory(self, agent_id: str) -> List[Dict[str, str]]:
        """
        获取指定智能体的所有短期明细记忆。
        """
        self._init_agent(agent_id)
        return self._short_term_memories[agent_id]

    def clear_short_term_memory(self, agent_id: str):
        """
        清理指定智能体的短期记忆。
        """
        if agent_id in self._short_term_memories:
            self._short_term_memories[agent_id] = []
            logger.info(f"Cleared short-term memory for agent '{agent_id}'.")

    def add_summary(self, agent_id: str, summary: str):
        """
        为智能体添加章节摘要，通常在章节切换时由外部 LLM 提炼后调用。
        """
        self._init_agent(agent_id)
        self._chapter_summaries[agent_id].append(summary)
        logger.info(f"Added chapter summary for agent '{agent_id}'.")

    def get_summaries(self, agent_id: str) -> List[str]:
        """
        获取指定智能体的所有历史章节摘要。
        """
        self._init_agent(agent_id)
        return self._chapter_summaries[agent_id]

    def convert_to_summary(self, agent_id: str, summary: str):
        """
        章节切换时的操作：将给定的摘要存入长期记录，并释放（清空）旧的明细短期记忆。
        """
        self.add_summary(agent_id, summary)
        self.clear_short_term_memory(agent_id)
        logger.info(f"Converted memory to summary and cleared short-term details for agent '{agent_id}'.")

    def get_full_context(self, agent_id: str) -> List[Dict[str, str]]:
        """
        获取智能体的完整上下文，将历史章节摘要打包为一个 system 提示，
        随后附上近期的短期记忆。
        """
        self._init_agent(agent_id)
        context = []
        summaries = self._chapter_summaries[agent_id]
        
        if summaries:
            combined_summary = "\n\n".join(
                [f"【第 {i+1} 章摘要】\n{s}" for i, s in enumerate(summaries)]
            )
            context.append({
                "role": "system",
                "content": f"以下是历史章节摘要（长线记忆）：\n{combined_summary}"
            })
            
        context.extend(self._short_term_memories[agent_id])
        return context

# 全局单例
memory_manager = MemoryManager()
