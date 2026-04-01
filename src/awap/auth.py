"""Authentication helpers."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from awap.domain import UserDefinition, UserRole
from awap.repository import WorkflowRepository

bearer_scheme = HTTPBearer(auto_error=False)


def require_role(
    repository: WorkflowRepository,
    *roles: UserRole,
) -> Callable[[HTTPAuthorizationCredentials | None], UserDefinition]:
    allowed = set(roles)

    def dependency(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> UserDefinition:
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing bearer token.",
            )
        user = repository.get_user_by_token(credentials.credentials)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid bearer token.",
            )
        if allowed and user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions.",
            )
        return user

    return dependency
