import json
from openai import AsyncOpenAI

class AIService:
    def __init__(self, settings):
        self.settings = settings
        self._clients = {}

    @property
    def client(self):
        from app.core.security import fingerprint
        from app.core.tenancy import organization_id
        key = self.settings.openai_api_key
        if not key:
            return None
        try:
            scope = organization_id()
        except PermissionError:
            scope = "platform"
        identity = (scope, fingerprint(key))
        if identity not in self._clients:
            self._clients[identity] = AsyncOpenAI(api_key=key, timeout=40, max_retries=1)
        return self._clients[identity]

    async def parse(self, schema, instructions, data):
        if not self.client:
            raise RuntimeError("openai_not_configured")
        response = await self.client.responses.parse(model=self.settings.openai_model,
            instructions=instructions, input=json.dumps(data, ensure_ascii=False, default=str),
            text_format=schema, store=False)
        if response.output_parsed is None:
            raise RuntimeError("openai_refused_or_incomplete")
        return response.output_parsed

    async def close(self):
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
