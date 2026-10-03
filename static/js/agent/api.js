export class ApiError extends Error {
  constructor(code, status) {
    super(code || 'request_failed');
    this.code = code || 'request_failed';
    this.status = status;
  }
}

export function createApi(csrfToken) {
  const parse = async response => response.json().catch(() => ({}));

  return {
    async get(url) {
      const response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const payload = await parse(response);
      if (!response.ok) throw new ApiError(payload.error, response.status);
      return payload;
    },

    async post(url, body = {}) {
      const response = await fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify(body),
      });
      const payload = await parse(response);
      if (!response.ok) throw new ApiError(payload.error || payload.decision?.reason_code, response.status);
      return payload;
    },

    async delete(url) {
      const response = await fetch(url, {
        method: 'DELETE', credentials: 'same-origin', headers: { Accept: 'application/json', 'X-CSRFToken': csrfToken },
      });
      const payload = await parse(response);
      if (!response.ok) throw new ApiError(payload.error, response.status);
      return payload;
    },
  };
}
