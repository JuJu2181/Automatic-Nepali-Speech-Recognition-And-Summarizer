import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
import tensorflow as tf
import tensorflow.keras.backend as K
import numpy as np
from sklearn.model_selection import train_test_split
import pandas as pd
import librosa
import time
from tqdm import tqdm
import edit_distance as ed
from datasets import load_metric


from model.configs import SR, device_name, UNQ_CHARS, INPUT_DIM, MODEL_NAME, NUM_UNQ_CHARS
from model.utils import CER_from_mfccs, batchify, clean_single_wav, gen_mfcc, indices_from_texts, load_model, calculateWER, calculateErrorRates, calculateErrorRatesAlt
from model.model import get_model


def calculateBatchErrorRates(output,target,start,end,cer,wer,isValidation=False):
    """
        The line of codes below is for computing evaluation metric (CER) on internal validation data.
    """
    input_len = np.ones(output.shape[0]) * output.shape[1]
    # Decode the output using beam search and CTC to get  the required logits
    with tf.device(device_name):
        decoded_indices = K.ctc_decode(output, input_length=input_len,
                            greedy=False, beam_width=100)[0][0]


    # decoded_indices = tf.nn.ctc_beam_search_decoder(inputs=output, sequence_length=input_len,beam_width=100)[0][0]
    
    # Remove the padding token from batchified target texts
    target_indices = [sent[sent != 0].tolist() for sent in target]

    # Remove the padding, unknown token, and blank token from predicted texts
    predicted_indices = [sent[sent > 1].numpy().tolist() for sent in decoded_indices] # idx 0: padding token, idx 1: unknown, idx -1: blank token

    batch_cer = cer
    batch_wer = wer
    len_batch = end - start
    predicted_labels = []
    actual_labels = []
    for i in range(len_batch):
        predicted_indices_list = predicted_indices[i]
        actual_indices_list = target_indices[i]
        predicted_label = "".join([UNQ_CHARS[index] for index in predicted_indices_list])
        actual_label = "".join([UNQ_CHARS[index] for index in actual_indices_list])
        if isValidation == True:
            # print(f"\nPred: {predicted_label}")
            # print(f"Actual: {actual_label}\n")
            predicted_labels.append(predicted_label)
            actual_labels.append(actual_label)
        error_rates = calculateErrorRatesAlt(actual_label,predicted_label)
        batch_cer += error_rates[0]
        batch_wer += error_rates[1]
    batch_cer /= len_batch
    batch_wer /= len_batch
    if isValidation == True:
        return batch_cer, batch_wer, predicted_labels, actual_labels
    return batch_cer,batch_wer

def train_model(model, optimizer, train_wavs, train_texts, validation_wavs, validation_texts, start_epoch= 0, end_epoch=1, batch_size=50,restore_checkpoint=True):

    with tf.device(device_name):
        # Definition of checkpoint
        checkpoint_dir = './training-checkpoints-new'
        checkpoint_prefix = os.path.join(checkpoint_dir,'ckpt')
        checkpoint = tf.train.Checkpoint(optimizer =optimizer, model = model)
        # restore latest checkpoint
        if restore_checkpoint == True:
            if len(os.listdir(checkpoint_dir)) != 0: 
                checkpoint.restore(tf.train.latest_checkpoint(checkpoint_dir))
                print('Checkpoint Restored')
            else:
                print("There are no checkpoints to restore")
        # These will be the final results to be returned
        train_losses = []
        validation_losses = []
        # train_CERs = []
        # train_WERs = []
        validation_CERs = []
        validation_WERs = []
        for e in range(start_epoch, end_epoch):
            epoch = e+1
            start_time = time.time()
            len_train = len(train_wavs)
            len_validation = len(validation_wavs)
            training_loss = 0
            training_CER = 0
            training_WER = 0
            validation_loss = 0
            validation_CER = 0
            validation_WER = 0
            train_batch_count = 0
            validation_batch_count = 0
            # Training Steps
            print("Training epoch: {}".format(epoch))
            for start in tqdm(range(0, len_train, batch_size)):

                end = None
                if start + batch_size < len_train:
                    end = start + batch_size
                else:
                    end = len_train
                x, target, target_lengths, output_lengths = batchify(
                    train_wavs[start:end], train_texts[start:end], UNQ_CHARS)

                with tf.GradientTape() as tape:
                    output = model(x, training=True)

                    loss = K.ctc_batch_cost(
                        target, output, output_lengths, target_lengths)

                grads = tape.gradient(loss, model.trainable_weights)
                optimizer.apply_gradients(zip(grads, model.trainable_weights))

                training_loss += np.average(loss.numpy())
                train_batch_count += 1
                # training_CER, training_WER = calculateBatchErrorRates(output,target,start,end,training_CER,training_WER)


            # Validation Step
            print("Validation epoch: {}".format(epoch))
            for start in tqdm(range(0, len_validation, batch_size)):

                end = None
                if start + batch_size < len_validation:
                    end = start + batch_size
                else:
                    end = len_validation
                x, target, target_lengths, output_lengths = batchify(
                    validation_wavs[start:end], validation_texts[start:end], UNQ_CHARS)

                output = model(x, training=False)

                # Calculate CTC Loss
                loss = K.ctc_batch_cost(
                    target, output, output_lengths, target_lengths)

                validation_loss += np.average(loss.numpy())
                validation_batch_count += 1
                validation_CER, validation_WER, predicted_labels, actual_labels = calculateBatchErrorRates(output,target,start,end,validation_CER,validation_WER,isValidation=True)
            print(f"\nTest Results for epoch {epoch}:\n")
            for pred_idx in range(0,len(predicted_labels)):
                print(f"\nPred: {predicted_labels[pred_idx].split()}")
                print(f"Actual: {actual_labels[pred_idx].split()}\n")
            
            # Average the results
            # losses
            training_loss /= train_batch_count
            validation_loss /= validation_batch_count
            # cers
            # training_CER /= train_batch_count
            validation_CER /= validation_batch_count
            # wers 
            # training_WER /= train_batch_count 
            validation_WER /= validation_batch_count

            # Append the results 
            train_losses.append(training_loss)
            # train_CERs.append(training_CER)
            # train_WERs.append(training_WER)
            validation_losses.append(validation_loss)
            validation_CERs.append(validation_CER)
            validation_WERs.append(validation_WER)
            
            # rec = f"Epoch: {epoch}, Train Loss: {training_loss:.2f}, Validation Loss: {validation_loss:.2f}, Train CER: {(training_CER*100):.2f}, Validation CER: {(validation_CER*100):.2f}, Train WER: {(training_WER*100):.2f}, Validation WER: {(validation_WER*100):.2f} in {(time.time() - start_time):.2f} secs\n"

            rec = f"Epoch: {epoch}, Train Loss: {training_loss:.2f}, Validation Loss: {validation_loss:.2f}, Validation CER: {(validation_CER*100):.2f}, Validation WER: {(validation_WER*100):.2f} in {(time.time() - start_time):.2f} secs\n"

            # rec = f"Epoch: {epoch}, Train Loss: {training_loss:.2f}, Validation Loss: {validation_loss:.2f} in {(time.time() - start_time):.2f} secs\n"

            print(rec)
            if epoch % 5 == 0:
                print(f"Now saving checkpoint for epoch {epoch}")
                checkpoint.save(checkpoint_prefix) 
                print('Checkpoint Saved')
                model.save(f'./trained_models/model_{epoch}.h5')
                print(f"Model saved for epoch {epoch}")


        result = {
            'epoch': list(range(start_epoch+1,end_epoch+1)),
            'train_loss': train_losses,
            'validation_loss': validation_losses,
            # 'train_cer': train_CERs,
            'validation_cer': validation_CERs,
            # 'train_wer': train_WERs,
            'validation_wer': validation_WERs
        }
    
    return model, result

# def load_data(wavs_dir, texts_dir):
#     texts_df = pd.read_csv(texts_dir)[0:20000]
#     train_wavs = []
#     print(f'There are {texts_df.shape[0]} files')
#     for idx,f_name in enumerate(texts_df["filename"]):
#         wav, _ = librosa.load(f"{wavs_dir}/{f_name}.flac", sr=SR)
#         train_wavs.append(wav)
#         index = idx + 1
#         if index % 10000 == 0:
#             print(f"{index} data loaded !!!")
#     train_texts = texts_df["label"].tolist()
#     return train_wavs, train_texts

def load_data_with_mfcc(texts_dir):
    # texts_df = pd.read_csv(texts_dir,skiprows=0,nrows=1000)
    texts_df = pd.read_csv(texts_dir)
    print(f"There are {texts_df.shape[0]} rows in dataset")
    train_texts = texts_df["label"].to_list()
    train_wavs = texts_df["mfcc"].to_list()
    print("Getting mfcc features from the audio waves")
    train_wavs = [np.fromstring(mfcc_str, sep=',',dtype=np.float32) for mfcc_str in train_wavs]
    return train_wavs, train_texts

def update_csv(result):
    print("Now updating csv file")
    original_csv_path = "./results_new_data1.csv"
    original_df = pd.read_csv(original_csv_path)
    df_temp = pd.DataFrame(result)
    # df_temp.rename(columns={"epochs":"epoch"},inplace=True)
    # df_temp["train_cer"] = df_temp["train_cer"]*100
    df_temp["validation_cer"] = df_temp["validation_cer"]*100
    # df_temp["train_wer"] = df_temp["train_wer"]*100
    df_temp["validation_wer"] = df_temp["validation_wer"]*100
    df_final = pd.concat([original_df,df_temp],ignore_index=True)
    print(f"Updated results dataframe now have {df_final.shape[0]} rows")
    df_temp.to_csv(original_csv_path,index=False)
    df_final.to_csv(original_csv_path,index=False)
    # df_final.to_csv("./results_new_data100.csv",index=False)
    print("Updated CSV saved")


if __name__ == "__main__":
    print(device_name)

    # Defintion of the model
    model = get_model(INPUT_DIM, NUM_UNQ_CHARS, num_res_blocks=5, num_cnn_layers=2,
                      cnn_filters=50, cnn_kernel_size=15, rnn_dim=170, rnn_dropout=0.15, num_rnn_layers=2,
                      num_dense_layers=1, dense_dim=340, model_name=MODEL_NAME, rnn_type="lstm",
                      use_birnn=True)
    # model = get_model(INPUT_DIM, NUM_UNQ_CHARS, num_res_blocks=3, num_cnn_layers=2,
    #                   cnn_filters=50, cnn_kernel_size=20, rnn_dim=180, rnn_dropout=0.2, num_rnn_layers=2,
    #                   num_dense_layers=1, dense_dim=360, model_name=MODEL_NAME, rnn_type="lstm",
    #                   use_birnn=True)
    print("Model defined \u2705 \u2705 \u2705 \u2705\n")

    # Defintion of the optimizer
    optimizer = tf.keras.optimizers.Adam()
    # No learning was seen even upto 15 epochs
    # optimizer = tf.keras.optimizers.SGD(learning_rate=0.003, momentum=0.9)


    #load data along with mfcc
    t1 = time.time()
    # Load the data
    print("Loading data.....")
    train_wavs, train_texts = load_data_with_mfcc(texts_dir="~/manualpartition/teamSaransha/data/data_cnn/data_first_90k.csv")
    t2 = time.time()
    print(f"Data loaded with mfcc \u2705 \u2705 \u2705 \u2705\nAnd It took {t2-t1} seconds\n")

    # Train Test Split
    """
    Originally the data was split in the 95% train and 5% test set; With total of 148K (audio,text) pairs.
    """
    t3 = time.time()
    print("Splitting data.....")
    train_wavs, test_wavs, train_texts, test_texts = train_test_split(
        train_wavs, train_texts, test_size=0.01,shuffle=False, random_state=None)
    t4 = time.time()
    print(f"Data splitted \u2705 \u2705 \u2705 \u2705\nAnd It took {t4-t3} seconds\n")
    # Train the model
    """
    Originally the model was trained for 58 epochs; With a batch size of 50.
    """
    # loading checkpoint
    # model = load_model('/content/drive/MyDrive/Training_Checkpoints/checkpoint_50.h5')
    t5 = time.time()
    print("Training Model.....")
    model_trained, result = train_model(model, optimizer, train_wavs, train_texts,
                test_wavs, test_texts, start_epoch=50,end_epoch=100, batch_size=64,restore_checkpoint=True)
    # model_trained.save('./model_final_sgd.h5')
    t6 = time.time()
    print(f"Model Trained and Saved \u2705 \u2705 \u2705 \u2705\nAnd It took {t6-t5} seconds\n")

    update_csv(result)
    print('Results updated \u2705 \u2705 \u2705 \u2705\n')
    print(f"Total time taken: {time.time() - t1} seconds")



    '''
    Some notes:
    Batch size - 500,300 gave GPU error
    Batch size - 5 was too slow took nearly 1 hr for an epoch
    Batch size - 250 increased gpu utilization without giving error and saw some improvement in training time
    '''
