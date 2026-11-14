from pydantic import BaseModel


class UserSchema(BaseModel):
    user_id: int
    user_name: str