import streamlit as st
import pandas as pd

# Intialize session state
if 'task_list' not in st.session_state:
    st.session_state['task_list'] = []

st.title('Simple Checklist App')

def add_task(task_name):
    st.session_state['task_list'].append({'Task': task_name, 'Completed': False})

def update_status(index, new_status):
    st.session_state['task_list'][index]['Completed'] = new_status

def delete_task(index):
    del st.session_state['task_list'][index]

# Add new task
with st.form('AddTaskForm', clear_on_submit=True):
    new_task = st.text_input('Add a new task', '')
    submitted = st.form_submit_button('Add Task')
    if submitted and new_task:
        add_task(new_task)

# Display current tasks
if st.session_state['task_list']:
    for index, task_info in enumerate(st.session_state['task_list']):
        col1, col2 = st.columns([0.8, 0.2])
        col1.checkbox(task_info['Task'], key=f'task-{index}', value=task_info['Completed'], 
                     on_change=update_status, args=(index, not task_info['Completed']))
        col2.button('Delete', key=f'del-{index}', on_click=delete_task, args=(index,))
else:
    st.write('No tasks added yet!')