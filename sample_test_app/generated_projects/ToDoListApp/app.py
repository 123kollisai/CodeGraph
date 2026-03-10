import streamlit as st
import pandas as pd

# Load the current todo list items from session state or initialize an empty dataframe
if 'todo_list' not in st.session_state:
    st.session_state.todo_list = pd.DataFrame(columns=['Task'])

def add_task(task):
    # This function adds a new task to the list
    new_data = pd.DataFrame({'Task': [task]})
    st.session_state.todo_list = pd.concat([st.session_state.todo_list, new_data], ignore_index=True)

def remove_task(index):
    # This function removes a task by index
    st.session_state.todo_list = st.session_state.todo_list.drop(index).reset_index(drop=True)

# App main interface
def main():
    st.title('To-Do List App')

    # Text box to enter new task
    new_task = st.text_input('What do you need to do?', '')
    if st.button('Add Task'):
        if new_task:
            add_task(new_task)
            st.success('Task added successfully!')

    # Display current todo list
    if not st.session_state.todo_list.empty:
        st.write('Current To-Do List:')
        task_list = st.session_state.todo_list
        for index, row in task_list.iterrows():
            task_action = st.button('Done', key=index)
            if task_action:
                remove_task(index)
            st.write(f'{index + 1}) {row['Task']}')

if __name__ == '__main__':
    main()