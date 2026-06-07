custom_css = """
    <style>
    div[data-testid="stVerticalBlock"] div:has(div.fixed-header) {
        position: sticky;
        top: 2.875rem;
        background-color: white;
        z-index: 999;
    }
    .fixed-header {
        border-bottom: 1px solid black;
    }
    .chat-container {
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        width: 100%;
    }
    .chat-bubble {
        max-width: 60%;
        padding: 10px;
        margin: 10px;
        border-radius: 10px;
        display: flex;
        align-items: center;
    }
    .chat-bubble.user {
        background-color: #CCE5FF;
        align-self: flex-end;
    }
    .chat-bubble.assistant {
        background-color: #E6E6E6;
        align-self: flex-start;
    }
    .chat-bubble img {
        width: 40px;
        height: 40px;
        border-radius: 50%;
        margin-right: 10px;
        object-fit: cover;
    }
    .user-container {
        display: flex;
        justify-content: flex-end;
        align-items: center;
    }
    .assistant-container {
        display: flex;
        justify-content: flex-start;
        align-items: center;
    }
    body {
        display: flex;
        justify-content: center;
        align-items: center;
        height: 100vh;
        margin: 0;
    }
    .scrollviewer {
        width: 80%;
        max-width: 300px;
        overflow-x: auto;
        border: 1px solid #ccc;
        border-radius: 10px;
        white-space: nowrap;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .scroll-content {
        display: inline-block;
    }
    .scroll-item {
        display: inline-block;
        margin: 10px;
    }
    .scroll-item img {
        width: 300px;
        height: 250px;
        object-fit: cover;
        display: block;
        border-radius: 10px;
    }
    .file-list-container {
        width: 700px;
        max-height: 300px;
        overflow-y: auto;
        border: 1px solid #acc;
        padding: 10px
    }
    </style>
"""
