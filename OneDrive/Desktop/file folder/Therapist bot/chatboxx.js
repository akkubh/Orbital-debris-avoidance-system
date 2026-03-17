document.addEventListener('DOMContentLoaded', function () {
    const messageContainer = document.getElementById('message-container');
    const chatForm = document.getElementById('chat-form');
    const userInput = document.getElementById('user-input');
 
    chatForm.addEventListener('submit', function (event) {
      event.preventDefault();
      const userMessage = userInput.value;
      displayMessage(userMessage, 'user');
      userInput.value = '';
      sendUserMessage(userMessage);
    });
 
    function displayMessage(message, role) {
      const messageElement = document.createElement('div');
      messageElement.classList.add('message', role + '-message');
      messageElement.innerText = message;
      messageContainer.appendChild(messageElement);
      messageContainer.scrollTop = messageContainer.scrollHeight;
    }
 
    function sendUserMessage(message) {
      fetch('http://127.0.0.1:5000/uppercase', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ text: message })
      })
      .then(response => response.json())
      .then(data => {
        const aiResponse = data.response.join('');
        displayMessage(aiResponse, 'ai');
      })
      .catch(error => {
        console.error('Error sending message:', error);
      });
    }
  });
