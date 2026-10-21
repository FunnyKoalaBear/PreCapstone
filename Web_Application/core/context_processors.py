from .permissions import ADMIN, OPERATOR, PLANNER, ROLES, role_level


def roles(request):
    level = role_level(request.user)
    return {'can': {r.lower(): level >= ROLES.index(r) for r in (OPERATOR, PLANNER, ADMIN)}}
