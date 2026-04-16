from pathlib import Path
from typing import List

from veadk import Agent
from core.config import config, BASE_DIR
from tools.robust_file_tools import list_directory, read_file, write_file
from core.memory import memory_manager
from core.logger import logger

def _load_prompt(prompt_file: str) -> str:
    prompt_path = BASE_DIR / prompt_file
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    else:
        logger.warning(f"Prompt file not found: {prompt_path}")
        return ""

class AgentFactory:
    """
    负责动态初始化作家、审阅及读者智能体，并挂载工具。
    """
    
    @classmethod
    def _create_agent(cls, name: str, description: str, prompt_key: str, default_prompt: str, config_role: str) -> Agent:
        prompt_file = config.get(prompt_key, default_prompt)
        instruction = _load_prompt(prompt_file)

        model_config = config.get_model_config(config_role)
        provider = model_config.get("provider") or config.get("model.agent.provider", "openai")
        model_name = model_config.get("model_name") or config.get("model.agent.name") or "doubao-seed-1-8-251228"
        api_base = model_config.get("api_base") or config.get("model.agent.api_base")
        api_key = model_config.get("api_key") or config.get("model.agent.api_key")
        temperature = model_config.get("temperature", 0.7)

        model_extra_config = {}
        timeout = model_config.get("timeout")
        if timeout not in (None, ""):
            model_extra_config["request_timeout"] = timeout
        max_retries = model_config.get("max_retries")
        if max_retries not in (None, ""):
            model_extra_config["num_retries"] = max_retries
        
        # 将 memory_manager 作为依赖或直接在 agent_factory 中处理
        # 挂载了文件操作工具
        agent = Agent(
            name=name,
            description=description,
            instruction=instruction,
            tools=[list_directory, read_file, write_file],
            model_provider=provider,
            model_name=model_name,
            model_api_base=api_base,
            model_api_key=api_key,
            model_extra_config=model_extra_config,
            temperature=temperature,
        )

        # VeADK 运行时通常使用全局模型环境变量，这里保留角色模型元数据以便排障和后续扩展。
        agent.model_runtime_config = model_config

        # 将记忆挂载在 agent 的自定义属性上，方便后续 runner/orchestrator 调用
        # 注意：VeADK 本身也有自己的 short_term_memory 参数传递给 Runner
        # 我们这里遵循任务要求：“为各智能体挂载短期记忆模块”
        agent.memory_manager = memory_manager
        
        logger.info(f"Initialized {name} agent with robust file tools and memory manager.")
        return agent

    @classmethod
    def create_writer(cls) -> Agent:
        return cls._create_agent(
            name="writer",
            description="作家智能体，负责根据大纲或设定创作小说内容。",
            prompt_key="prompts.writer",
            default_prompt="screenwriter_system_prompt.md",
            config_role="writer"
        )

    @classmethod
    def create_reviewer(cls) -> Agent:
        return cls._create_agent(
            name="reviewer",
            description="专业审阅专员，负责去 AI 味并进行专业评审。",
            prompt_key="prompts.reviewer",
            default_prompt="reader_ai_content_reviewer_6_system_prompt.md",
            config_role="reviewer"
        )

    @classmethod
    def create_readers(cls) -> List[Agent]:
        readers = []
        for i in range(1, 6):
            config_role = f"reader_{i}" if config.get(f"models.reader_{i}") else "reader"
            agent = cls._create_agent(
                name=f"reader_{i}",
                description=f"读者智能体 {i}，从特定读者视角提供反馈。",
                prompt_key=f"prompts.reader_{i}",
                default_prompt=f"reader_persona_{i}.md",
                config_role=config_role
            )
            readers.append(agent)
        return readers

    @classmethod
    def create_orchestrator(cls, sub_agents: List[Agent]) -> Agent:
        prompt_file = config.get("prompts.orchestrator", "")
        instruction = _load_prompt(prompt_file) if prompt_file else "你是流程管控智能体。"

        model_config = config.get_model_config("orchestrator")
        provider = model_config.get("provider") or config.get("model.agent.provider", "openai")
        model_name = model_config.get("model_name") or config.get("model.agent.name") or "doubao-seed-1-8-251228"
        api_base = model_config.get("api_base") or config.get("model.agent.api_base")
        api_key = model_config.get("api_key") or config.get("model.agent.api_key")
        temperature = model_config.get("temperature", 0.1)

        model_extra_config = {}
        timeout = model_config.get("timeout")
        if timeout not in (None, ""):
            model_extra_config["request_timeout"] = timeout
        max_retries = model_config.get("max_retries")
        if max_retries not in (None, ""):
            model_extra_config["num_retries"] = max_retries

        agent = Agent(
            name="orchestrator",
            description="流程管控智能体，负责整个小说生成与评审环节的调度。",
            instruction=instruction,
            tools=[list_directory, read_file, write_file],
            sub_agents=sub_agents,
            model_provider=provider,
            model_name=model_name,
            model_api_base=api_base,
            model_api_key=api_key,
            model_extra_config=model_extra_config,
            temperature=temperature,
        )
        agent.model_runtime_config = model_config
        agent.memory_manager = memory_manager
        logger.info("Initialized orchestrator agent.")
        return agent

agent_factory = AgentFactory()
