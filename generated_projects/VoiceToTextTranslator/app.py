import streamlit as st
import speech_recognition as sr

r = sr.Recognizer()

st.title('Voice to Text Translator')

with st.sidebar:
    st.header('Instructions')
    st.write('Click the record button and start speaking to convert your voice to text.')

audio_input = st.file_uploader('Upload your audio here', type=['wav', 'mp3', 'mp4'])
if audio_input is not None:
    st.audio(audio_input, format='audio/wav')

    with sr.AudioFile(audio_input) as source:
        audio_data = r.record(source)
        text = r.recognize_google(audio_data)
        st.text_area('Here is the text from your audio:', text, height=300)