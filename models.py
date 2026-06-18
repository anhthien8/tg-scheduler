"""
Pydantic models for API request/response validation.
"""
from pydantic import BaseModel
from typing import Optional


class SendCodeRequest(BaseModel):
    phone: str
    account_id: int


class ConfigRequest(BaseModel):
    api_id: str
    api_hash: str
    phone: str


class VerifyCodeRequest(BaseModel):
    phone: str
    code: str
    phone_code_hash: str
    account_id: int
    password: Optional[str] = None


class AccountCreate(BaseModel):
    phone: str
    name: Optional[str] = None        # auto-filled from TG profile if not set
    api_id: Optional[str] = None      # uses TG Desktop default if not set
    api_hash: Optional[str] = None    # uses TG Desktop default if not set
    proxy_url: Optional[str] = None   # e.g. socks5://user:pass@host:port


class MessageSchema(BaseModel):
    msg_order: int = 0
    msg_type: str = "text"
    content: Optional[str] = None
    media_path: Optional[str] = None
    poll_question: Optional[str] = None
    poll_options: Optional[str] = None
    poll_multiple: bool = False


class TargetSchema(BaseModel):
    chat_id: int
    chat_title: Optional[str] = None
    chat_type: Optional[str] = None


class ScheduleCreate(BaseModel):
    account_id: int = 1
    name: str
    schedule_type: str  # hourly, daily, weekly, monthly, once
    time_of_day: str  # HH:MM
    days_of_week: Optional[str] = None
    day_of_month: Optional[int] = None
    once_date: Optional[str] = None
    max_sends: Optional[int] = None  # null = unlimited
    is_active: bool = True
    messages: list[MessageSchema] = []
    targets: list[TargetSchema] = []


class ScheduleUpdate(ScheduleCreate):
    pass
