"""记忆后端注册表。每个后端模块暴露一个 `create(config) -> Memory` 工厂函数。"""

BACKEND_REGISTRY: dict[str, str] = {
    "builtin": "mmsg.memory.backends.builtin",
}


def get_backend_factory(name: str):
    import importlib

    module_path = BACKEND_REGISTRY.get(name)
    if module_path is None:
        raise ValueError(f"未知记忆后端 '{name}'，可用: {list(BACKEND_REGISTRY)}")
    mod = importlib.import_module(module_path)
    return mod.create
