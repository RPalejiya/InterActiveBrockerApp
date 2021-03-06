

# -*- coding: utf-8 -*-
"""
Created on Fri Jan 22 10:41:42 2021

@author: rpalejiya
"""


# Import libraries
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import (Contract, ComboLeg)
from ibapi.order import Order
import threading
import time
import datetime
import pandas as pd
import random
import os
import asyncio

class TradeApp(EWrapper, EClient): 
    def __init__(self): 
        EClient.__init__(self, self) 
        EWrapper.__init__(self)
        self.histdata = {}
        self.liveStkdata = [0,0,0,0,0]
        self.nextOrderId = 0
        self.symbol=""
        #Order Thresholds
        self.rewardtorisk=5   # Max Theoritical profit / Price  # This is also use to initialize Min Prices in dpdmatrix matrix
        self.TransactionCost =15 # Dont sell anything lower than the threshold
        self.volatilitypercentthreshold = 4     # Percent of the stock price. So value of 1 for Stock of $800 DPD should be less than 8. So Option expiring in 2 days will have distance of 16. It will allow bFly in range of prce+-16
        self.starttime =  time.time()
        self.position_df = pd.DataFrame(columns=['Account', 'Symbol', 'SecType','Currency', 'Position', 'Avg cost'])
        self.orderstatus_df = pd.DataFrame(columns=["OrderId","Status","Filled","FillPrice","LastFillPrice" ,"PermId","ClientId","WhyHeld","MktCapPrice"])

    def nextValidId(self, orderId):
        super().nextValidId(orderId)
        print("NextValidId:", orderId)
        self.nextOrderId = orderId

        

    def initBflyRange (self, expdate :str,  low :int, high :int, step :int, delta :int):
        self.delta = delta  #Butterfly Prie Delta
        self.targetprices = range(low, high+step, step)      
        self.strikepricedata = {}
        self.dpdmatrix = {}  
        self.optconid = {}
        self.expdate=expdate 
        self.daysToExp= (datetime.datetime.strptime(expdate,'%Y%m%d').date() - datetime.date.today()).days + 1
        self.clientorderId = self.clientId*1000
        self.orderlog=pd.DataFrame({'Symbol':self.symbol, 'DayToExp':self.daysToExp, 'C/P':'','Action':"Startup",'Strike1':0,'Strike2':0,'Strike3':0,'LimitPrice':0, 'rewardtorisk': 0,'Stkprice': 0,'Time': time.time()}
                                    , index=[self.clientId]) 
        self.buyThreshold = round(delta*100/self.rewardtorisk)  
        self.sellThreshold = round(delta*100/max( 5, (self.rewardtorisk/2)) )
         #populate dummy rows outside of range upto delta
        for dummyrows in range (list(self.targetprices)[0]- 6*delta, list(self.targetprices)[0], step):
                                            #OptBid, OptASk, [Bfly1/2/3 -Ask, Bid, HistLowAsk, HistHighBid, AvgAsk, AvgBid ], OptLast 
                                                                #NOTE: We IGNORE Bfly's HistHighAsk and HistLowBid. That is not usefull for buy or sell decesion. 
                                                                # Buy action is related to Ask price of Bfly (not ask of Option)
                                                                # Sell action is related to Bid price hence order is changed for Bfly
            self.strikepricedata[dummyrows] =[-1000000000, 1000000000   
                                       , [1000000000,0,1000000,0, 0,0 ]   
                                       , [1000000000,0, 1000000,0, 0,0 ] 
                                       , [1000000000,0, 1000000,0, 0,0 ] 
                                       , 0 ] #initialize strikepricedata once before  
        for dummyrows in range (list(self.targetprices)[-1], list(self.targetprices)[-1]+ 6*delta+step , step):
            self.strikepricedata[dummyrows] =[-1000000000, 1000000000   
                                       , [1000000000,0, 1000000,0, 0,0 ]   
                                       , [1000000000,0, 1000000,0, 0,0 ] 
                                       , [1000000000,0, 1000000,0, 0,0 ] 
                                       , 0] #initialize strikepricedata once before  
                          
        for targetprice in self.targetprices:     
            self.strikepricedata[targetprice] =[-1000000000, 1000000000   
                                        , [1000000000,0, 1000000,0, 0,0 ]   
                                        , [1000000000,0, 1000000,0, 0,0 ] 
                                        , [1000000000,0, 1000000,0, 0,0 ] 
                                        , 0 ] #initialize strikepricedata once before      
            self.optconid[targetprice] = ""

        for distperday in range(0,high-low,1):
                                            #{distperday: [HistLowAsk, HistHighBid, AvgAsk, AvgBid] ... }
            self.dpdmatrix[distperday] = [[1*self.buyThreshold ,1*self.sellThreshold, 0, 0 ],[2*self.buyThreshold ,2*self.sellThreshold, 0, 0 ],[3*self.buyThreshold ,3*self.sellThreshold, 0, 0 ] ]   
            self.dpdmatrix[-distperday] =[[1*self.buyThreshold ,1*self.sellThreshold, 0, 0 ],[2*self.buyThreshold ,2*self.sellThreshold, 0, 0 ],[3*self.buyThreshold ,3*self.sellThreshold, 0, 0 ] ]   
      
        
  ###Contract, Crantdetails & Order
    def usStk(self,symbol,sec_type="STK",currency="USD",exchange="SMART"):
        contract = Contract()
        self.symbol = symbol 
        contract.symbol = symbol
        contract.secType = sec_type
        contract.currency = currency
        contract.exchange = exchange
        return contract      
    def usOpt(self,symbol, strike , right = "C"):
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "OPT"
        contract.currency = "USD"
        contract.exchange = "SMART"
        contract.right = right
        contract.strike = "{}".format(strike)
        contract.lastTradeDateOrContractMonth = self.expdate
        contract.multiplier = "100"
        return contract 
    def usBfly(self,symbol, strike1, strike2, strike3 ):
        if strike1 < strike2 < strike3  : 
            contract = Contract()
            contract.symbol = symbol
            contract.secType = "BAG"
            contract.currency = "USD"
            contract.exchange = "SMART"
            leg1 = ComboLeg()
            leg1.conId = self.optconid[strike1]
            leg1.ratio = 1
            leg1.action = "BUY"
            leg2 = ComboLeg()
            leg2.conId = self.optconid[strike2]  
            leg2.ratio = 2
            leg2.action = "SELL"
            leg3 = ComboLeg()
            leg3.conId = self.optconid[strike3]
            leg3.ratio = 1
            leg3.action = "BUY"
            contract.comboLegs = []
            contract.comboLegs.append(leg1)
            contract.comboLegs.append(leg2) 
            contract.comboLegs.append(leg3) 
            return contract 

    def limitorder (self,action, totalQuantity ,lmtPrice):
        order = Order()
        order.action = action
        order.orderType = "LMT"
        order.totalQuantity = totalQuantity
        order.lmtPrice = lmtPrice
        return order
        
                    
       #Bid, ASk, [Bfly1/2/3 -Ask, Bid, HistLowAsk, HistHighBid, AvgAsk, AvgBid ] 
     #{distperday: [HistLowAsk, HistHighBid, AvgAsk, AvgBid] ... }
    async def BflyBidchange (self, strike, i):  # Bid matters for selling Bfly
        wvapdist = round((strike- round(float(self.liveStkdata[-1]),2)),2) 
        distperday = int(round(wvapdist / self.daysToExp )) 
        leg1=strike-(i+1)*(self.delta)
        leg3=strike+(i+1)*(self.delta)         
        p =  self.strikepricedata[leg1][0] - 2*self.strikepricedata[strike][1] + self.strikepricedata[leg3][0]           
        if ((i+1)*self.delta)*(-50) < p <((i+1)*self.delta)*150:  # Dont do anything if price is smaller or larger than 50% of spread. Its likely miscalculation
             self.strikepricedata[strike][i+2][1]= p  #Bfly Bid
             BflyAvgBid=self.strikepricedata[strike][i+2][5]
             if BflyAvgBid==0:
                 self.strikepricedata[strike][i+2][5] =  p #Seed Bfly AvgBid
             else:
                 self.strikepricedata[strike][i+2][5] =  round((9*BflyAvgBid +p)/10 ,2) #Update Bfly AvgBid
             DPDAvgBid=self.dpdmatrix[distperday][i][3] 
             if DPDAvgBid==0:
                 self.dpdmatrix[distperday][i][3] = p
             else:
                 self.dpdmatrix[distperday][i][3] = round((p+9*DPDAvgBid)/10 ,2)     #DPD AvgBid
            
             revenue=p-self.TransactionCost
             allowevolatilityddist= min(10,self.daysToExp) * (self.volatilitypercentthreshold/100) *  float(self.liveStkdata[-1])  
             if (   abs(wvapdist) < allowevolatilityddist and
                     revenue > (i+1)*self.sellThreshold and
                    # Make sure price + transaction cost is less than  
                     revenue > DPDAvgBid*1.1 and #half of DPD AvgBid
                     revenue > BflyAvgBid*1.1 and #half StrikePrice AvgBid
                     revenue > (self.strikepricedata[strike][i+2][4])    #and AvgAsk
                ):  #Bid, ASk, [Bfly1/2/3 -Ask, Bid, HistLowAsk, HistHighBid, AvgAsk, AvgBid ] 
                if ( (time.time() - self.starttime) <600  or #10 mins to initialize
                     (leg1 not in self.optconid) or (strike not in self.optconid) or (leg3 not in self.optconid) or self.optconid[leg1]=="" or self.optconid[strike]=="" or self.optconid[leg3]==""
                    ): 
                    None #print("One of the option contract ID is not valid for order: {} Bfly ({} -2*{} {}) for {} USD".format(self.symbol,strike1, strike2, strike3, price ))       
                    #print("Initializing data for {}".format(time.time() - self.starttime)) 
                elif self.clientorderId-(self.clientId*1000) >=20 :
                    print ("Reached Max order limit of {}".format(self.clientorderId-(self.clientId*1000)) )
                    self.printstats()
                    raise SystemExit(0)
                else:
                    print("Sell: {} C Exp:{} Bid:{} Ask:{}".format(self.symbol ,self.expdate, self.strikepricedata[strike][i+2][1], self.strikepricedata[strike][i+2][0] ) )
                    print("\t Sell {}: {},{}".format( leg1  , self.strikepricedata[leg1][0]  , self.strikepricedata[leg1][1]) )
                    print("\t Buy  {}: {},{}".format( strike, self.strikepricedata[strike][0], self.strikepricedata[strike][1]) ) 
                    print("\t Sell {}: {},{}".format( leg3  , self.strikepricedata[leg3][0]  , self.strikepricedata[leg3][1]) )
                    print("\t Revenue:{} HistHighBid:{} AvgAsk:{} AvgBid:{} ".format(revenue, self.strikepricedata[strike][i+2][3] ,self.strikepricedata[strike][i+2][4],self.strikepricedata[strike][i+2][5]   ))  
                    print("\t DPD:{} HistLowBid:{} AvgBid:{} WvapDist:{} ".format(distperday, self.dpdmatrix[distperday][i][1], self.dpdmatrix[distperday][i][3], wvapdist   ) )
                    print("\t Risk/Reward: {}/{} ".format((i+1)*(self.delta)*100,revenue) )
                    self.clientorderId =  self.clientorderId +1
                    contract= self.usBfly(self.symbol,leg1, strike, leg3 )
                    order_obj= self.limitorder ("SELL", 1, (revenue-1)/100 )  #$1 lessthan ask
#                    self.placeOrder(self.clientorderId, contract, order_obj)
                    print("Placed Sell order id:{} {} Bfly (-{} +2*{} -{}) for {} USD".format(self.clientorderId,self.symbol,leg1, strike, leg3, revenue ))            
                    ol=pd.DataFrame({'Symbol':self.symbol, 'DayToExp':self.daysToExp, 'C/P':'C','Action':"SELL",'Strike1':leg1,'Strike2':strike,'Strike3':leg3,'LimitPrice':revenue, 'rewardtorisk':self.rewardtorisk ,'Stkprice':self.liveStkdata[0],'Time': time.time()}
                                    , index=[self.clientId])
                    self.orderlog=self.orderlog.append(ol)
              
    async def BflyAskchange (self, strike, i):  # Ask matters for Buying Bfly
        wvapdist = round((strike- round(float(self.liveStkdata[-1]),2)),2) 
        distperday = int(round(wvapdist / self.daysToExp )) 
        leg1=strike-(i+1)*(self.delta)
        leg3=strike+(i+1)*(self.delta)         
        p = self.strikepricedata[leg1][1] - 2*self.strikepricedata[strike][0] + self.strikepricedata[leg3][1]           
        if ((i+1)*self.delta)*(-50) < p <((i+1)*self.delta)*150:  # Dont do anything if price is smaller or larger than 50% of spread. Its likely miscalculation
             self.strikepricedata[strike][i+2][0]= p  #Bfly Ask
             BflyAvgAsk=self.strikepricedata[strike][i+2][4]
             if BflyAvgAsk==0:
                 self.strikepricedata[strike][i+2][4] =  p 
             else:
                 self.strikepricedata[strike][i+2][4] =  round((9*BflyAvgAsk +p)/10 ,2) #Update Bfly AvgAsk
             DPDAvgAsk=self.dpdmatrix[distperday][i][2] 
             if DPDAvgAsk==0:
                 self.dpdmatrix[distperday][i][2] = p
             else:    
                 self.dpdmatrix[distperday][i][2] = round((p+9*DPDAvgAsk)/10 ,2)     #DPD AvgAsk
               
             cost=p+self.TransactionCost
             allowevolatilityddist= min(10,self.daysToExp) * (self.volatilitypercentthreshold/100) *  float(self.liveStkdata[-1])  
             if (cost < (self.strikepricedata[strike][i+2][5] + (BflyAvgAsk - self.strikepricedata[strike][i+2][5] )*.25  ) ): 
                 print("Buy: {} C {}:{}-{}-{} cost{} wvapdist:{} alloweddist:{} dpdAvgAsk:{} BflyAvgAsk:{} AvgBid:{}".format(self.symbol ,self.expdate,leg1,strike,leg3, cost, wvapdist, allowevolatilityddist,DPDAvgAsk,BflyAvgAsk,(self.strikepricedata[strike][i+2][5]) ) )
             if (p < 0 or   #Buy if cost is 0 dont worry about Fees 
                (abs(wvapdist) < allowevolatilityddist  and
                    cost < ((i+1)*(self.delta))*100/self.rewardtorisk and
                    #  Make sure price + transaction cost is less than  
                    cost < DPDAvgAsk/1.20  and #20 less of DPD AvgAsk
                    cost < BflyAvgAsk/1.20  and #20% less of StrikePrice AvgAsk
                    cost < (self.strikepricedata[strike][i+2][5]  + (BflyAvgAsk - self.strikepricedata[strike][i+2][5] )*.25  )    #and AvgBid + 25% of spread
                )):  #Bid, ASk, [Bfly1/2/3 -Ask, Bid, HistLowAsk, HistHighBid, AvgAsk, AvgBid ] 
                if ( (time.time() - self.starttime) <600 or #10 mins to initialize
                     (leg1 not in self.optconid) or (strike not in self.optconid) or (leg3 not in self.optconid) or self.optconid[leg1]=="" or self.optconid[strike]=="" or self.optconid[leg3]==""
                    ): 
                    None #print("One of the option contract ID is not valid for order: {} Bfly ({} -2*{} {}) for {} USD".format(self.symbol,strike1, strike2, strike3, price ))       
                    #print("Initializing data for {}".format(time.time() - self.starttime)) 
                elif self.clientorderId-(self.clientId*1000) >=20 :
                    print ("Reached Max order limit of {}".format(self.clientorderId-(self.clientId*1000)) )
                    self.printstats()
                    raise SystemExit(0)
                else:
                    print("Buy: {} C Exp:{} Bid:{} Ask:{}".format(self.symbol ,self.expdate, self.strikepricedata[strike][i+2][1], self.strikepricedata[strike][i+2][0] ) )
                    print("\t Buy  {}: {},{}".format( leg1 , self.strikepricedata[leg1][0], self.strikepricedata[leg1][1]) )
                    print("\t Sell {}: {},{}".format( strike, self.strikepricedata[strike][0], self.strikepricedata[strike][1]) ) 
                    print("\t Buy  {}: {},{}".format( leg3, self.strikepricedata[leg3][0], self.strikepricedata[leg3][1]) )
                    print("\t Cost:{} HistLowAsk:{} AvgAsk:{} AvgBid:{} ".format(cost, self.strikepricedata[strike][i+2][2] ,self.strikepricedata[strike][i+2][4],self.strikepricedata[strike][i+2][5]   ))  
                    print("\t DPD:{} HistLowAsk:{} AvgAsk:{} WvapDist:{} ".format(distperday, self.dpdmatrix[distperday][i][0], self.dpdmatrix[distperday][i][2], wvapdist   ) )
                    print("\t Reward/Risk: {}/{} ".format((i+1)*(self.delta)*100,cost ))
                    self.clientorderId =  self.clientorderId +1
                    contract= self.usBfly(self.symbol,leg1, strike, leg3 )
                    order_obj= self.limitorder ("BUY", 1, (cost+1)/100 )  #$1 morethan ask
#                    self.placeOrder(self.clientorderId, contract, order_obj)
                    print("Placed Buy order id:{} {} Bfly ({} -2*{} {}) for {} USD".format(self.clientorderId,self.symbol,leg1, strike, leg3, cost ))            
                    ol=pd.DataFrame({'Symbol':self.symbol, 'DayToExp':self.daysToExp, 'C/P':'C','Action':"BUY",'Strike1':leg1,'Strike2':strike,'Strike3':leg3,'LimitPrice':cost, 'rewardtorisk':self.rewardtorisk ,'Stkprice':self.liveStkdata[0],'Time': time.time()}
                                    , index=[self.clientId])
                    self.orderlog=self.orderlog.append(ol)
                    
    def optBidchange(self, reqId):  #Change in 1 Option Bid tick changes Ask for same strke Bfly and Bid for adjacent butterfly 
        for i in [0,1,2]:
            bfly1=reqId-(i+1)*(self.delta/2)
            bfly2=reqId 
            bfly3=reqId+(i+1)*(self.delta/2)
            asyncio.run(self.BflyBidchange(bfly1,i))
            asyncio.run(self.BflyAskchange(bfly2,i))
            asyncio.run(self.BflyBidchange(bfly3,i))
            
    def optAskchange(self, reqId):  #Change in 1 Option Ask tick changes Bid for same strke Bfly and Ask for adjacent butterfly 
        for i in [0,1,2]:
            bfly1=reqId-(i+1)*(self.delta/2)
            bfly2=reqId 
            bfly3=reqId+(i+1)*(self.delta/2)
            asyncio.run(self.BflyAskchange(bfly1,i))
            asyncio.run(self.BflyBidchange(bfly2,i))
            asyncio.run(self.BflyAskchange(bfly3,i))
       
  ###Wrapper Callbacks    
    def error(self, reqId, errorCode, errorString):
        print("Error. Id: " , reqId, " Code: " , errorCode , " Msg: " , errorString)
    def contractDetails(self, reqId: int, contractDetails):
        if reqId in self.optconid:
            self.optconid[reqId] = contractDetails.contract.conId
            #print(reqId, contractDetails.contract)
    def orderStatus(self, orderId, status, filled,remaining, avgFillPrice, permId,parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        super().orderStatus(orderId, status, filled, remaining,
                            avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice)
        dictionary = {"OrderId":orderId, "Status": status, "Filled":filled
                        ,"FillPrice":avgFillPrice,"LastFillPrice":lastFillPrice ,"PermId": 4
                        , "ClientId":clientId, "WhyHeld":whyHeld}  
        self.orderstatus_df = self.orderstatus_df.append (dictionary, ignore_index=True)
        
    def position(self, account, contract, position, avgCost):
        super().position(account, contract, position, avgCost)
        dictionary = {"Account":account, "Symbol": contract.symbol, "SecType": contract.secType,
                      "Currency": contract.currency, "Position": position, "Avg cost": avgCost}
        self.position_df = self.position_df.append(dictionary, ignore_index=True)
        
    
    def tickString(self, reqId, tickType, value):
        super().tickString(reqId, tickType, value)
        if reqId==0 and tickType==48:
            values = value.split(";")
            if values[1] != "0" :           
               #print(values)   #Last Price, Last Size, Total volume,   Time,   VWAP
               self.liveStkdata=[(values[0]), values[1], values[3], values[2], values[-2] ]
            #print("TickString. TickerId:", reqId, "Type:", tickType, "Last Price:", values[0], "Last Size:", values[1], "Time:", values[2], "VWAP:", values[-2])
            #print (time.time() ,  values[2])            #to Measure latency.
 
                               
        
    def tickPrice(self, reqId, tickType, price, attrib):
        super().tickPrice(reqId, tickType, price, attrib)
        #if reqId==800: print("OptTickPrice. TickerId:", reqId, "tickType:", tickType, "Price:", price, "Time:", time.time() )
        if reqId!=0:  # Process Only Option Data
            if tickType==1 and price!=-1 : #bid price
                #print("Start {} {} {}".format(self.clientId, reqId, time.time() ) )
                self.strikepricedata[reqId][0] = round(price*100,2)
                self.optBidchange(reqId) 
                #self.optBidchange(reqId) 
                #print("End {} {} {}".format(self.clientId, reqId, time.time() ) )
                
            elif tickType==2 and price!=-1 : #ask price
                self.strikepricedata[reqId][1] = round(price*100,2)
                self.optAskchange(reqId)
                #self.optAskchange(reqId)
                
            elif tickType==4 and price!=-1 : #Last price is Last element of list :)
                self.strikepricedata[reqId][-1] = round(price*100,2)

    def historicalData(self, reqId, bar):
        if reqId not in self.histdata:
            self.histdata[reqId] = [{"Date":bar.date,"Open":bar.open,"High":bar.high,"Low":bar.low,"Close":bar.close,"Volume":bar.volume}]
        else:
            self.histdata[reqId].append({"Date":bar.date,"Open":bar.open,"High":bar.high,"Low":bar.low,"Close":bar.close,"Volume":bar.volume})
        print("reqID:{}, date:{}, open:{}, high:{}, low:{}, close:{}, volume:{}".format(reqId,bar.date,bar.open,bar.high,bar.low,bar.close,bar.volume))

    #####Outside Routines
    def printstats(self):
        self.reqOpenOrders()
        self.reqPositions()
        dpd=self.getdpdstat()
        bfly=self.getbflystat()
        time.sleep(5)
        # print("==============Open Posistions=============")
        # print(self.position_df )
        # print("==============Open Orders=================")
        # print(self.orderstatus_df)
        fname="{}/{}_{}_OrderLog_{}.csv".format(os.getcwd(),self.symbol,self.expdate,self.daysToExp)
        if not os.path.isfile(fname):
           print(self.orderlog)
           self.orderlog.to_csv(fname, header='column_names')
        else: # else it exists so append without writing the header
           self.orderlog.to_csv(fname, mode='a')
        fname="{}/{}_{}_DPD_{}.csv".format(os.getcwd(),self.symbol,self.expdate,self.daysToExp)
        if not os.path.isfile(fname):
           dpd.to_csv(fname, header='column_names')
        else: # else it exists so append without writing the header
           dpd.to_csv(fname, mode='a')
        fname="{}/{}_{}_BFLY_{}.csv".format(os.getcwd(),self.symbol,self.expdate,self.daysToExp)
        if not os.path.isfile(fname):
           bfly.to_csv(fname, header='column_names')
        else: # else it exists so append without writing the header
           dpd.to_csv(fname, mode='a', header=False)
        print("Stats extracted in {}".format(os.getcwd() ))

    def getdpdstat(self):
        HistLowAsk1=[]
        AvgAsk1=[]
        HistHighBid1=[]
        AvgBid1=[]
        HistLowAsk2=[]
        AvgAsk2=[]
        HistHighBid2=[]
        AvgBid2=[]
        HistLowAsk3=[]
        AvgAsk3=[]
        HistHighBid3=[]
        AvgBid3=[]
        days=[]
        price=[]
        dpdmatrix=self.dpdmatrix
        alloweddist= round(min(5,self.daysToExp) * (self.volatilitypercentthreshold/100) * round(float(self.liveStkdata[0])) +1)
        for dpd in range(-alloweddist, alloweddist,1):
            HistLowAsk1.append(dpdmatrix[dpd][0][0])
            AvgAsk1.append(dpdmatrix[dpd][0][2])    
            HistHighBid1.append(dpdmatrix[dpd][0][1])
            AvgBid1.append(dpdmatrix[dpd][0][3])    
            HistLowAsk2.append(dpdmatrix[dpd][1][0])
            AvgAsk2.append(dpdmatrix[dpd][1][2])    
            HistHighBid2.append(dpdmatrix[dpd][1][1])
            AvgBid2.append(dpdmatrix[dpd][1][3])    
            HistLowAsk3.append(dpdmatrix[dpd][2][0])
            AvgAsk3.append(dpdmatrix[dpd][2][2])    
            HistHighBid3.append(dpdmatrix[dpd][2][1])
            AvgBid3.append(dpdmatrix[dpd][2][3])    
            days.append(self.daysToExp)
            price.append(self.liveStkdata[-1])
        df=pd.DataFrame({"HistLowAsk1":HistLowAsk1,"AvgAsk1":AvgAsk1,"HistHighBid1":HistHighBid1
                             ,"HistLowAsk2":HistLowAsk2,"AvgAsk2":AvgAsk2,"HistHighBid2":HistHighBid2
                             ,"HistLowAsk3":HistLowAsk3,"AvgAsk3":AvgAsk3,"HistHighBid3":HistHighBid3
                             , "DaysToExp": days, "VWAP": price }
                        ,index=range(-alloweddist, alloweddist,1))
        return df

    def getbflystat(self):
        CurrentAsk1=[]
        AvgAsk1=[]
        HistLowAsk1=[]
        Last1=[]
        currentBid1=[]
        AvgBid1=[]
        HistHighBid1=[]
        
        CurrentAsk2=[]
        AvgAsk2=[]
        HistLowAsk2=[]
        Last2=[]
        currentBid2=[]
        AvgBid2=[]
        HistHighBid2=[]
        
        CurrentAsk3=[]
        AvgAsk3=[]
        HistLowAsk3=[]
        Last3=[]
        currentBid3=[]
        AvgBid3=[]
        HistHighBid3=[]
        
        days=[]
        price=[]
        strikepricedata=self.strikepricedata   #Bid, ASk, [Bfly1/2/3 -Ask, Bid, HistLowAsk, HistHighBid, AvgAsk, AvgBid ] 
        for strike in self.targetprices :
            CurrentAsk1.append(strikepricedata[strike][2][0])
            AvgAsk1.append(strikepricedata[strike][2][4])
            HistLowAsk1.append(strikepricedata[strike][2][2])
            Last1.append( strikepricedata[strike-self.delta][-1] - 2*strikepricedata[strike][-1] + strikepricedata[strike+self.delta][-1] ) 
            currentBid1.append(strikepricedata[strike][2][1])
            AvgBid1.append(strikepricedata[strike][2][5])
            HistHighBid1.append(strikepricedata[strike][2][3])
            
            CurrentAsk2.append(strikepricedata[strike][3][0])
            AvgAsk2.append(strikepricedata[strike][3][4])
            HistLowAsk2.append(strikepricedata[strike][3][2])
            Last2.append( strikepricedata[strike-2*self.delta][-1] - 2*strikepricedata[strike][-1] + strikepricedata[strike+2*self.delta][-1] )
            currentBid2.append(strikepricedata[strike][3][1])
            AvgBid2.append(strikepricedata[strike][3][5])
            HistHighBid2.append(strikepricedata[strike][3][3])
            
            CurrentAsk3.append(strikepricedata[strike][4][0])
            AvgAsk3.append(strikepricedata[strike][4][4])
            HistLowAsk3.append(strikepricedata[strike][4][2])
            Last3.append( strikepricedata[strike-3*self.delta][-1] - 2*strikepricedata[strike][-1] + strikepricedata[strike+3*self.delta][-1] )
            currentBid3.append(strikepricedata[strike][4][1])
            AvgBid3.append(strikepricedata[strike][4][5])
            HistHighBid3.append(strikepricedata[strike][4][3])
            
            days.append(self.daysToExp)
            price.append(self.liveStkdata[-1])
        df=pd.DataFrame({ "CurrentAsk1":CurrentAsk1, "AvgAsk1":AvgAsk1 #,"HistLowAsk1":HistLowAsk1
                         ,"Last1":Last1,"currentBid1":currentBid1,"AvgBid1":AvgBid1 #,"HistHighBid1":HistHighBid1
                         ,"CurrentAsk2":CurrentAsk2, "AvgAsk2":AvgAsk2 #,"HistLowAsk2":HistLowAsk2
                         ,"Last2":Last2,"currentBid2":currentBid2,"AvgBid2":AvgBid2 #,"HistHighBid2":HistHighBid2
                         ,"CurrentAsk3":CurrentAsk3, "AvgAsk3":AvgAsk3 #,"HistLowAsk3":HistLowAsk3
                         ,"Last3":Last3,"currentBid3":currentBid3,"AvgBid3":AvgBid3 #,"HistHighBid3":HistHighBid3
                         , "DaysToExp": days, "VWAP": price }
                        ,index=self.targetprices  )
        return df

################################################################

def runscanner (TradeApp_obj , ticker, expdate, min :int, max :int, step :int, delta :int) :
    TradeApp_obj = TradeApp()
    TradeApp_obj.connect(host='127.0.0.1', port=7496, clientId=random.randint(0,1000)) #port 7497 paper trading/7496 for TWS LIVE
    print("ClientID:%s serverVersion:%s connectionTime:%s" % ( TradeApp_obj.clientId, TradeApp_obj.serverVersion(), TradeApp_obj.twsConnectionTime()))
    time.sleep(3)
    def websocket_con1():
        TradeApp_obj.run()
    con_thread = threading.Thread(target=websocket_con1, daemon=True)
    con_thread.start()
    print("connecting")
    for i in range(30):
        print(".")
        time.sleep(1)
        if TradeApp_obj.nextOrderId > 0 :break
    if TradeApp_obj.nextOrderId ==0: 
        print("Can not connect in 10 secs")
        raise SystemExit(0)
    
    try:
        TradeApp_obj.reqMktData(reqId=0, contract=TradeApp_obj.usStk(symbol=ticker) ,
                                          genericTickList="233", snapshot=False,regulatorySnapshot=False, mktDataOptions=[])
    except Exception as e:
        print(e)
        raise
               
    TradeApp_obj.initBflyRange(expdate,min,max,step,delta)
    for targetprice in TradeApp_obj.targetprices:     
        try:  
            TradeApp_obj.reqMktData(reqId=targetprice, contract=TradeApp_obj.usOpt(symbol=ticker, strike=targetprice) ,
                                          genericTickList="233", snapshot=False,regulatorySnapshot=False, mktDataOptions=[])
            
            TradeApp_obj.reqContractDetails(reqId=targetprice, contract=TradeApp_obj.usOpt(symbol=ticker, strike=targetprice))
        except Exception as e:
            print(e)
            raise
            
    time.sleep(5)  
    return TradeApp_obj


# runscanner ("A","GME", "20210129", 10,600,5,100)
# runscanner ("B","GME", "20210205", 10,600,5,100)    
    


D=runscanner ("D","TSLA", "20210212", 650,900,5,10)    
E=runscanner ("E","TSLA", "20210219", 650,900,5,10)    



D.reqGlobalCancel()

time.sleep(150)
for i in [D,E]:
    i.printstats()
    
    
bfly1=D.getbflystat()
dpd1=D.getdpdstat()
 
bfly2=E.getbflystat()
dpd2=E.getdpdstat()

bfly3=X.getbflystat()
dpd3=X.getdpdstat()
 
bfly4=Y.getbflystat()
dpd4=Y.getdpdstat()

optID=D.optconid
Z= E.dpdmatrix
C.reqOpenOrders()
o=C.orderlog

print(C.getbflystat())
print(C.getdpdstat())
print(C.dpdmatrix)
#for i in [C,D,E]:
#    dpd=i.getdpdstat()
#    cwd = os.getcwd()
#    path = cwd + "/new"
#    dpd.to_csv('{}\\DPD_stat_{}{}_{}.csv'.format(os.getcwd(),D.symbol,D.expdate,D.daysToExp))

#for i in [C,D,E]:
#    i.disconnect()    
        
