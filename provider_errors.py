class ProviderError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def public_error(exc):
    if isinstance(exc, ProviderError):
        return exc
    name = type(exc).__name__
    if name == "TwoFactorRequired":
        return ProviderError("two_factor", "Enter your authenticator code, then submit again.")
    if name in {"BadPassword", "BadCredentials", "InvalidUser", "UserNotFound"}:
        return ProviderError("credentials", "Instagram did not accept this login. Check your details in Instagram.")
    if "Challenge" in name or "Consent" in name or name in {"FeedbackRequired", "CheckpointRequired"}:
        return ProviderError("verification", "Instagram requires an account check. Complete it in the Instagram app. This connection has stopped.")
    if "Throttl" in name or name in {"PleaseWaitFewMinutes", "RateLimitError", "ClientForbiddenError"}:
        return ProviderError("restricted", "Instagram restricted this request. Collection has stopped; try again later in Instagram.")
    if name in {"LoginRequired", "ClientLoginRequired"}:
        return ProviderError("expired", "Instagram ended this session. Disconnect and sign in again.")
    if "NotFound" in name:
        return ProviderError("unavailable", "Instagram no longer makes this story or its viewer list available.")
    return ProviderError("provider", "Instagram could not complete this request. No complete comparison was produced.")


