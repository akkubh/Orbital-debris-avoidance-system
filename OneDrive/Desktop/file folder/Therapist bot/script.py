from flask import Flask, request, jsonify
from flask_cors import CORS
from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage




app = Flask(__name__)
CORS(app)  
api_key= "OzqLUCwf69PEjVJOAg3tWBzQ3vYtMH8U"
model= "mistral-small"
client= MistralClient(api_key)




@app.route('/uppercase', methods=['POST'])
def getResponse():
    data = request.get_json()
    text = data['text']
    prompt="Context: use all your medical knowledge to act as a psychologist. User prompt: "+text
    messages= [
    ChatMessage(role ="user", content=prompt)
    ]
    chat_response = client.chat(
    model=model,
    messages=messages,
    )
    string = chat_response.choices[0].message.content
    response_list = list(string)
    return jsonify({'response': response_list})


if __name__ == '__main__':
    app.run(debug=True)
