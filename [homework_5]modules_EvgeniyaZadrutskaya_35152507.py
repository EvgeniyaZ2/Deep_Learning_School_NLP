import random
import torch
from torch import nn
from torch.nn import functional as F

def softmax(x, temperature=10): # use your temperature
    e_x = torch.exp(x / temperature)
    return e_x / torch.sum(e_x, dim=0)

class Encoder(nn.Module):
    def __init__(self, input_dim, emb_dim, hid_dim, n_layers, dropout, bidirectional):
        super().__init__()
        
        self.input_dim = input_dim
        self.emb_dim = emb_dim
        self.hid_dim = hid_dim
        self.n_layers = n_layers
        self.dropout = dropout
        self.bidirectional = bidirectional
        
        self.embedding = nn.Embedding(input_dim, emb_dim)
        
        self.rnn = nn.LSTM(emb_dim, hid_dim, num_layers=n_layers, dropout=dropout, bidirectional=bidirectional)
        
        self.dropout = nn.Dropout(p=dropout)
        
    def forward(self, src):
        
        #src = [src sent len, batch size]
        
        # Compute an embedding from the src data and apply dropout to it
        embedded = self.dropout(self.embedding(src))
        
        #embedded = [src sent len, batch size, emb dim]
        
        # Compute the RNN output values of the encoder RNN. 
        # outputs, hidden and cell should be initialized here. Refer to nn.LSTM docs ;)
        
        outputs, (hidden, cell) = self.rnn(embedded)
        
        #outputs = [src sent len, batch size, hid dim * n directions]
        #hidden = [n layers * n directions, batch size, hid dim]
        #cell = [n layers * n directions, batch size, hid dim]
        
        #outputs are always from the top hidden layer
        #if self.bidirectional:
            
        return outputs, hidden, cell

class Attention(nn.Module):
    def __init__(self, enc_hid_dim, dec_hid_dim, bidirectional):
        super().__init__()
        
        self.enc_hid_dim = enc_hid_dim
        self.dec_hid_dim = dec_hid_dim
        
        self.attn = nn.Linear((1 + bidirectional)*enc_hid_dim + dec_hid_dim, enc_hid_dim)
        self.v = nn.Linear(enc_hid_dim, 1)
        
    def forward(self, hidden, encoder_outputs):
        
        # encoder_outputs = [src sent len, batch size, enc_hid_dim]
        # hidden = [1, batch size, dec_hid_dim] hidden последнего слоя, поэтому первая размерность 1!
        
        # repeat hidden and concatenate it with encoder_outputs
        hidden = hidden.repeat(encoder_outputs.shape[0], 1, 1) # [1, batch size, dec_hid_dim] -> [src sent len, batch size, dec_hid_dim]
        conc = torch.cat((encoder_outputs, hidden), dim=2) # [src sent len, batch size, enc_hid_dim + dec_hid_dim]

        # calculate energy
        energy = F.tanh(self.attn(conc)) # [src sent len, batch size, enc_hid_dim]
        
        # get attention, use softmax function which is defined, can change temperature
        a_t = softmax(self.v(energy)) # [src sent len, batch size, 1]  
        
        return a_t # the weights for the encoder hidden states 
    
    
class DecoderWithAttention(nn.Module):
    def __init__(self, output_dim, emb_dim, enc_hid_dim, dec_hid_dim, dropout, bidirectional, attention):
        super().__init__()

        self.emb_dim = emb_dim
        self.enc_hid_dim = enc_hid_dim
        self.dec_hid_dim = dec_hid_dim
        self.output_dim = output_dim
        self.attention = attention
        
        self.embedding = nn.Embedding(output_dim, emb_dim) 
        
        self.rnn = nn.GRU(emb_dim + (1 + bidirectional)*enc_hid_dim, dec_hid_dim, dropout=dropout)  # use GRU
        
        self.out = nn.Linear(emb_dim + (1 + bidirectional)*enc_hid_dim + dec_hid_dim, output_dim)  # linear layer to get next word
        
        self.dropout = nn.Dropout(dropout)
        
    def forward(self, input, hidden, encoder_outputs):
        #input = [batch size]
        #hidden = [n layers * n directions, batch size, hid dim]
        
        #n directions in the decoder will both always be 1, therefore:
        #hidden = [n layers, batch size, hid dim] ->
        #hidden = [1, batch size, dec_hid_dim] hidden последнего слоя, поэтому первая размерность 1!
        
        input = input.unsqueeze(0) # because only one word, no words sequence 
        
        #input = [1, batch size]
        
        embedded = self.dropout(self.embedding(input)) # [1, batch size] -> [1, batch size, emb_dim]
        
        #embedded = [1, batch size, emb dim]
        
        # get weighted sum of encoder_outputs
        a_t = self.attention(hidden, encoder_outputs) # [src sent len, batch size, 1] 
        # encoder_outputs: [src sent len, batch size, enc_hid_dim * n directions]
        w_t = torch.sum(a_t * encoder_outputs, dim=0, keepdim=True) # w_t = [1, batch size, enc_hid_dim * n directions]
        # concatenate weighted sum and embedded, break through the GRU
        # torch.cat((embedded, w_t), dim=2) - [1, batch size, emb_dim + enc_hid_dim * n directions]
        output, hidden = self.rnn(torch.cat((embedded, w_t), dim=2), hidden) # [1, batch size, dec_hid_dim]
        # get predictions
        prediction = self.out(torch.cat((embedded, w_t, output), dim=2))  # prediction = [1, batch size, output dim]
        # prediction = F.relu(prediction)
        prediction = prediction.squeeze(0)  # prediction = [batch size, output dim]
        
        return prediction, hidden
        

class Seq2Seq(nn.Module):
    def __init__(self, encoder, decoder, device):
        super().__init__()
        
        self.encoder = encoder
        self.decoder = decoder
        self.device = device

        # if encoder.bidirectional:
        #     assert encoder.hid_dim * 2 == decoder.dec_hid_dim, \
        #         "Hidden dimensions of encoder and decoder must be equal!"
        # else:
        #     assert encoder.hid_dim == decoder.dec_hid_dim, \
        #             "Hidden dimensions of encoder and decoder must be equal!"
        
    def forward(self, src, trg, teacher_forcing_ratio = 0.5):
        
        # src = [src sent len, batch size]
        # trg = [trg sent len, batch size]
        # teacher_forcing_ratio is probability to use teacher forcing
        # e.g. if teacher_forcing_ratio is 0.75 we use ground-truth inputs 75% of the time
        
        # Again, now batch is the first dimention instead of zero
        batch_size = trg.shape[1]
        trg_len = trg.shape[0]
        trg_vocab_size = self.decoder.output_dim
        
        #tensor to store decoder outputs
        outputs = torch.zeros(trg_len, batch_size, trg_vocab_size).to(self.device)
        
        #last hidden state of the encoder is used as the initial hidden state of the decoder
        enc_states, hidden, cell = self.encoder(src)

        hidden = hidden[-1].unsqueeze(0) # hidden последнего слоя
        # hidden = torch.cat((hidden[-2], hidden[-1]), dim=1).unsqueeze(0) # hidden последнего слоя
        
        #first input to the decoder is the <sos> tokens
        input = trg[0,:]
        
        for t in range(1, trg_len):

            output, hidden = self.decoder(input, hidden, enc_states)

            outputs[t] = output
            #decide if we are going to use teacher forcing or not
            teacher_force = random.random() < teacher_forcing_ratio
            #get the highest predicted token from our predictions
            top1 = output.argmax(-1) 
            #if teacher forcing, use actual next token as next input
            #if not, use predicted token
            input = trg[t] if teacher_force else top1
        
        return outputs