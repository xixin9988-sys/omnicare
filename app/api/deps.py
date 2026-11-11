from fastapi import Request


def get_supervisor(request: Request):
    return request.app.state.supervisor
