const input = document.getElementById("message-input");
const chatBox = document.getElementById("chat-box");
const typing = document.getElementById("typing");
const sendButton = document.getElementById("send-btn");

function addMessage(message, sender) {
    const messageDiv = document.createElement("div");
    messageDiv.classList.add("message", sender);

    const avatar = document.createElement("div");
    avatar.classList.add("avatar");
    avatar.textContent = sender === "bot" ? "🤖" : "👤";

    const content = document.createElement("div");
    content.classList.add("message-content");

    const paragraph = document.createElement("p");
    paragraph.textContent = message;

    content.appendChild(paragraph);
    messageDiv.appendChild(avatar);
    messageDiv.appendChild(content);
    chatBox.appendChild(messageDiv);

    chatBox.scrollTop = chatBox.scrollHeight;
}

async function sendMessage() {
    const message = input.value.trim();

    if (!message) return;

    addMessage(message, "user");
    input.value = "";
    typing.classList.remove("hidden");
    sendButton.disabled = true;

    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({message: message})
        });

        const data = await response.json();
        addMessage(data.reply, "bot");
    } catch (error) {
        console.error(error);
        addMessage("Sorry, I couldn't connect to the server.", "bot");
    } finally {
        typing.classList.add("hidden");
        sendButton.disabled = false;
        input.focus();
    }
}

input.addEventListener("keydown", function(event) {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
});

function newChat() {
    chatBox.innerHTML = "";
    addMessage(
        "Hello! 👋 I'm your AI Assistant. How can I help you today?",
        "bot"
    );
    input.focus();
}
