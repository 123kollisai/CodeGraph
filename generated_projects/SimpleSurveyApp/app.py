import streamlit as st
import pandas as pd

# Title of the survey app
st.title('Simple Survey App')

# Define a simple form with a few input elements
def survey_form():
    with st.form('survey_form', clear_on_submit=True):
        name = st.text_input('Name')
        age = st.number_input('Age', min_value=18, max_value=100, step=1)
        feedback = st.text_area('Feedback')
        submit_button = st.form_submit_button('Submit')

        if submit_button:
            return name, age, feedback

# Display the results in a table
results = []
result = survey_form()
if result:
    results.append(result)
    results_df = pd.DataFrame(results, columns=['Name', 'Age', 'Feedback'])
    st.table(results_df)
