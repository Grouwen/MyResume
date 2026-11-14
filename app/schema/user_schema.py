from pydantic import BaseModel


class UserSchema(BaseModel):
    user_id: int
    user_name: str

class LoginUserSchema(BaseModel):
    user_email: str
    code: int