import truststore

# Use Windows system certificates
truststore.inject_into_ssl()

from flask import Flask, render_template, request, jsonify
from google import genai
from dotenv import load_dotenv
import os
import time
# Load .env from the same folder as app.py
load_dotenv(
    os.path.join(os.path.dirname(__file__), ".env"),
    override=True
)

app = Flask(__name__)

# Get Gemini API key
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("GEMINI_API_KEY is missing in .env file")

# Create Gemini client
client = genai.Client(api_key=api_key)

# Use a model listed in Google's official API documentation
MODEL = "gemini-3.8-flash"


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(silent=True) or {}
        user_message = data.get("message", "").strip()

        if not user_message:
            return jsonify({
                "reply": "Please enter a message."
            }), 400

        max_attempts = 3
        last_error = None

        for attempt in range(max_attempts):
            try:
                print(
                    f"Trying model: {MODEL} | "
                    f"Attempt: {attempt + 1}/{max_attempts}"
                )

                response = client.models.generate_content(
                    model=MODEL,
                    contents=user_message
                )

                reply = response.text

                if reply:
                    return jsonify({
                        "reply": reply
                    }), 200

                print("Gemini returned an empty response.")

                return jsonify({
                    "reply": "Gemini returned an empty response. Please try again."
                }), 502

            except Exception as e:
                last_error = e

                error_text = str(e)
                error_type = type(e).__name__

                print(
                    f"{MODEL} failed: {error_type}: {error_text}"
                )

                # Retry only for temporary server/service errors.
                is_temporary_error = (
                    "503" in error_text
                    or "UNAVAILABLE" in error_text
                    or "429" in error_text
                    or "RESOURCE_EXHAUSTED" in error_text
                    or "ConnectError" in error_type
                )

                if not is_temporary_error:
                    break

                if attempt < max_attempts - 1:
                    wait_time = 2 ** (attempt + 1)
                    print(
                        f"Temporary error. "
                        f"Retrying in {wait_time} seconds..."
                    )
                    time.sleep(wait_time)

        print("FINAL ERROR:", repr(last_error))

        error_text = str(last_error)

        if "CERTIFICATE_VERIFY_FAILED" in error_text:
            message = (
                "SSL certificate verification failed. "
                "Please check your network or certificate configuration."
            )
            status_code = 502

        elif "429" in error_text or "RESOURCE_EXHAUSTED" in error_text:
            message = (
                "Gemini API quota or rate limit reached. "
                "Please check your API usage and try again later."
            )
            status_code = 429

        elif "503" in error_text or "UNAVAILABLE" in error_text:
            message = (
                "Gemini service is temporarily unavailable. "
                "Please try again later."
            )
            status_code = 503

        else:
            message = (
                "Gemini request failed. "
                "Check the terminal for the detailed error."
            )
            status_code = 502

        return jsonify({
            "reply": message
        }), status_code

    except Exception as e:
        print("ERROR TYPE:", type(e).__name__)
        print("ERROR:", repr(e))

        return jsonify({
            "reply": "Something went wrong. Please check the server terminal."
        }), 500


if __name__ == "__main__":
    app.run(debug=True)