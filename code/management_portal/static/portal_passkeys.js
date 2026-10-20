(function (global) {
  "use strict";

  function decodeBase64Url(value) {
    const normalized = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(normalized + "=".repeat((4 - normalized.length % 4) % 4));
    return Uint8Array.from(binary, character => character.charCodeAt(0));
  }

  function encodeBase64Url(value) {
    if (value == null) return null;
    const bytes = new Uint8Array(value);
    let binary = "";
    bytes.forEach(byte => { binary += String.fromCharCode(byte); });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  function creationOptions(options) {
    return {
      ...options,
      challenge: decodeBase64Url(options.challenge),
      user: { ...options.user, id: decodeBase64Url(options.user.id) },
      excludeCredentials: (options.excludeCredentials || []).map(item => ({
        ...item,
        id: decodeBase64Url(item.id),
      })),
    };
  }

  function requestOptions(options) {
    return {
      ...options,
      challenge: decodeBase64Url(options.challenge),
      allowCredentials: (options.allowCredentials || []).map(item => ({
        ...item,
        id: decodeBase64Url(item.id),
      })),
    };
  }

  function serializedCredential(credential) {
    const response = credential.response;
    const serialized = {
      id: credential.id,
      rawId: encodeBase64Url(credential.rawId),
      type: credential.type,
      authenticatorAttachment: credential.authenticatorAttachment || null,
      clientExtensionResults: credential.getClientExtensionResults?.() || {},
      response: {
        clientDataJSON: encodeBase64Url(response.clientDataJSON),
      },
    };
    if (response.attestationObject) {
      serialized.response.attestationObject = encodeBase64Url(response.attestationObject);
      serialized.response.transports = response.getTransports?.() || [];
    } else {
      serialized.response.authenticatorData = encodeBase64Url(response.authenticatorData);
      serialized.response.signature = encodeBase64Url(response.signature);
      serialized.response.userHandle = encodeBase64Url(response.userHandle);
    }
    return serialized;
  }

  async function post(url, payload) {
    if (!global.PortalAPI) throw new Error("Portal JSON transport is unavailable.");
    return global.PortalAPI.post(url, payload || {}, { detectLoginRedirect: false });
  }

  function userFacingError(error) {
    if (error?.name === "NotAllowedError") return "Passkey request was cancelled or timed out.";
    if (error?.name === "InvalidStateError") return "This Passkey is already registered.";
    if (error?.name === "SecurityError") return "Passkeys require a secure Portal connection.";
    return error?.message || "Passkey request failed.";
  }

  async function register(payload) {
    const started = await post("/api/account/passkeys/registration/options", payload);
    const credential = await navigator.credentials.create({
      publicKey: creationOptions(started.options),
    });
    if (!credential) throw new Error("Passkey registration was cancelled.");
    return post("/api/account/passkeys/registration/verify", {
      ceremony_token: started.ceremony_token,
      credential: serializedCredential(credential),
      name: payload.name || "Passkey",
    });
  }

  async function authenticate({ mediation = "required", signal } = {}) {
    const started = await post("/api/auth/passkeys/options", {});
    const credential = await navigator.credentials.get({
      publicKey: requestOptions(started.options),
      mediation,
      ...(signal ? { signal } : {}),
    });
    if (!credential) throw new Error("Passkey authentication was cancelled.");
    return post("/api/auth/passkeys/verify", {
      ceremony_token: started.ceremony_token,
      credential: serializedCredential(credential),
    });
  }

  global.PortalPasskeys = {
    supported: () => Boolean(global.PublicKeyCredential && navigator.credentials),
    conditionalSupported: async () => {
      if (!global.PublicKeyCredential?.isConditionalMediationAvailable) return false;
      try {
        return Boolean(await global.PublicKeyCredential.isConditionalMediationAvailable());
      } catch (_error) {
        return false;
      }
    },
    register,
    authenticate,
    userFacingError,
  };
})(window);
