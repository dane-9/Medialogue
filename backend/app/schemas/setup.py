from pydantic import BaseModel


class SetupStep(BaseModel):
    key: str
    title: str
    complete: bool
    optional: bool = True
    detail: str
    settings_tab: str | None = None


class SetupStatusResponse(BaseModel):
    wizard_complete: bool
    wizard_required: bool
    steps: list[SetupStep]


class SetupCompleteRequest(BaseModel):
    complete: bool = True
