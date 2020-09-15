This is a Keras implementation of "A 12-Lead ECG Arrhythmia Classification Method Based on 1D Densely Connected CNN" paper and a 3rd prize solution of "The First China ECG Intelligent Competition".

### Contents

- final_train folder:  
contains the training code.

- final_run_semi folder:  
contains the predicting code of semi-final.

- final_run_final folder:  
contains the predicting code of final.


The codes contains the following components:

* Python scripts:
   -- challenge.py (necessary) -  add your codes to classify normal and diseases.For ease of evaluation, you should pay attention to the following points:
   1.You need to write the results into "answers.csv",and save it in the current folder
   2.You need to write your test data path with the argparse parameter
   In short, challenge.py is your test code to make predictions or inferences. Please refer to this demo file for details.


* BASH scripts:
   -- run.sh (necessary) - a script calls "challenge.py" to generate "answers.csv", you can modify the --test_path parameter in this file
     
	 
* CSV files:
   -- answers.csv (necessary) - a text file containing the prediction results.

* README.txt - this file

* Other files:
     These files support to run the bash file and the challenge.py, such as your codes to run the model, and the model file, etc.

We verify that your code is working as you intended, by running "run.sh" on the test set, then comparing the results with references.
