from __future__ import print_function

import datetime
import json
import math
import os
import pickle
import sys
import time
from collections import namedtuple
from random import randint

import discord
from discord.ext import commands
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from pytz import timezone
from web3 import Web3

from utils import slp_utils

if not os.path.isfile("config.json"):
    sys.exit("'config.json' not found! Add it and try again.")
else:
    with open("config.json") as file:
        config = json.load(file)


def has_roles(context):
    roles = [role.name for role in context.message.author.roles]
    if "Admin" in roles:
        return True
    return False


class Claim(commands.Cog, name="claim"):
    def __init__(self, bot):
        self.bot = bot
        self.Transaction = namedtuple("Transaction", "from_address to_address amount")
        self.Payout = namedtuple("Payout",
                                 "name private_key nonce slp_balance scholar_transaction academy_transaction fee_transaction")
        self.SlpClaim = namedtuple("SlpClaim",
                                   "name address private_key slp_claimed_balance slp_unclaimed_balance state")
        self.nonces = {}
        # self.web3 = Web3(Web3.HTTPProvider('https://proxy.roninchain.com/free-gas-rpc'))
        self.slp_claims = {}

    @staticmethod
    def parseRoninAddress(address):
        assert (address.startswith("ronin:"))
        return Web3.toChecksumAddress(address.replace('ronin:', "0x"))

    @staticmethod
    def formatRoninAddress(address):
        return address.replace("0x", 'ronin:')

    @commands.command(name="claim", description=f"Claim SLP. Syntax: '<prefix>claim'")
    async def claim_slp(self, context):
        try:
            scholar = config['accounts']['scholars'][str(context.author.id)]
        except KeyError:
            await context.reply(embed=discord.Embed(color=0xff000,
                                                    description="You can't use this command yet. Please contact the admin."))
            return
        scholar_name = scholar["Name"]
        account_address = self.parseRoninAddress(scholar["AccountAddress"])

        slp_unclaimed_balance = slp_utils.get_unclaimed_slp(account_address)
        nonce = self.nonces[account_address] = slp_utils.web3.eth.get_transaction_count(account_address)
        if slp_unclaimed_balance > 0:
            confirmation_msg = await context.reply(content=context.author.mention, embed=discord.Embed(
                description=f"`{scholar_name}`(nonce: **{nonce}**) has **{slp_unclaimed_balance}** unclaimed SLP.\n\n"
                            f"*Message `Yes` if you want to claim SLP.*"))

            confirmation = await self.bot.wait_for('message', timeout=60,
                                                   check=lambda message: message.author.id == context.author.id)
            if confirmation.content.lower() == 'yes' and confirmation.channel == context.channel:
                await confirmation.delete()
                self.slp_claims[f"{context.author.id}"] = self.SlpClaim(
                    name=scholar_name,
                    address=account_address,
                    private_key=scholar["PrivateKey"],
                    slp_claimed_balance=slp_utils.get_claimed_slp(account_address),
                    slp_unclaimed_balance=slp_unclaimed_balance,
                    state={"signature": None})

                embed = discord.Embed(content=context.author.mention,
                                      description=f"*Claiming SLP for `{scholar_name}`...*",
                                      color=randint(0, 0x000ff))
                tz = timezone('EST')
                embed.timestamp = datetime.datetime.now(tz=tz)
                await confirmation_msg.edit(embed=embed)
                slp_claim = self.slp_claims[f'{context.author.id}']
                try:
                    slp_utils.execute_slp_claim(slp_claim, self.nonces)

                    if slp_claim.state["signature"] is None:
                        await confirmation_msg.edit(content=context.author.mention, embed=discord.Embed(
                            description=f"**Claiming SLP for <@!{context.author.id}> did not succeed! `{slp_claim.name}` has `{slp_claim.slp_unclaimed_balance}` unclaimed SLP. Please try again.**",
                            color=randint(0, 0x000ff)))
                        return
                    await confirmation_msg.edit(content=context.author.mention, embed=discord.Embed(
                        description=f"**Claimed SLP for <@!{context.author.id}>!**",
                        color=randint(0, 0x000ff)))
                    return
                except:
                    await confirmation_msg.edit(content=context.author.mention,
                                                embed=discord.Embed(
                                                    description=f"There was an error while claiming SLP for `{scholar_name}`.",
                                                    color=randint(0, 0x000ff)))
                    return
        else:
            await context.reply(embed=discord.Embed(color=0xff000,
                                                    description=f'The unclaimed balance is `{slp_unclaimed_balance}` for `{scholar_name}`'))

            # if slp_claim.state['signature'] is not None:
            #     slp_total_balance = slp_utils.get_claimed_slp(account_address)
            #     if slp_total_balance >= slp_claim.slp_claimed_balance + slp_claim.slp_unclaimed_balance:
            #         self.slp_claims.pop(str(context.author.id))
            #         await confirmation_msg.edit(content=context.author.mention, embed=discord.Embed(
            #             description=f"*Claimed SLP for <@!{context.author.id}>!*",
            #             color=randint(0, 0x000ff)))
            # await confirmation_msg.edit(content=context.author.mention,
            #                             embed=discord.Embed(
            #                                 description=f"*There was an error while claiming SLP for `{scholar_name}`. {slp_claim.name} has {slp_claim.slp_unclaimed_balance} unclaimed SLP. Please retry!*",
            #                                 color=randint(0, 0x000ff)))
            # return

    @commands.command(name="sendslp",
                      description=f"SLP payout. Syntax: '<prefix>sendslp'.'")
    async def send_slp(self, context):
        try:
            scholar = config['accounts']['scholars'][str(context.author.id)]
        except KeyError:
            await context.reply(content=context.author.mention, embed=discord.Embed(color=0xff000,
                                                                                    description="You can't use this command yet. Please contact the admin."))
            return
        scholar_name = scholar["Name"]
        account_address = self.parseRoninAddress(scholar["AccountAddress"])
        scholar_payout_address = self.parseRoninAddress(scholar["ScholarPayoutAddress"])

        slp_balance = slp_utils.get_claimed_slp(account_address)
        if slp_balance == 0:
            await context.reply(content=context.author.mention, embed=discord.Embed(color=randint(0, 0xff000),
                                                                                    description=f"Can't execute command for `{scholar_name}` because SLP balance is zero."))
            return

        scholar_payout_percentage = scholar["ScholarPayoutPercentage"]
        assert (0 <= scholar_payout_percentage <= 1)

        fee_payout_amount = math.floor(slp_balance * config['accounts']['fee_payout_percentage'])
        slp_balance_minus_fees = slp_balance - fee_payout_amount
        scholar_payout_amount = math.ceil(slp_balance_minus_fees * scholar_payout_percentage)
        academy_payout_amount = slp_balance_minus_fees - scholar_payout_amount

        assert (scholar_payout_amount >= 0)
        assert (academy_payout_amount >= 0)
        assert (slp_balance == scholar_payout_amount + academy_payout_amount + fee_payout_amount)

        payout = self.Payout(name=scholar_name,
                             private_key=scholar["PrivateKey"],
                             slp_balance=slp_balance,
                             nonce=slp_utils.web3.eth.get_transaction_count(account_address),
                             scholar_transaction=self.Transaction(from_address=account_address,
                                                                  to_address=scholar_payout_address,
                                                                  amount=scholar_payout_amount),
                             academy_transaction=self.Transaction(from_address=account_address,
                                                                  to_address=self.parseRoninAddress(
                                                                      config['accounts']["academy_payout_address"]),
                                                                  amount=academy_payout_amount),
                             fee_transaction=self.Transaction(from_address=account_address,
                                                              to_address=self.parseRoninAddress(
                                                                  (config['accounts']['fee_payout_address'])),
                                                              amount=fee_payout_amount))

        embed = discord.Embed(color=randint(0, 0xff000), description=f"**Payout for `{payout.name}`**\n\n"
                                                                     f"**SLP balance:**     `{payout.slp_balance} SLP`\n"
                                                                     f"**Nonce:**           `{payout.nonce}`\n\n"
                                                                     f"**Scholar Payout**\n Send `{payout.scholar_transaction.amount:5} SLP` from `{self.formatRoninAddress(payout.scholar_transaction.from_address)}` to `{self.formatRoninAddress(payout.scholar_transaction.to_address)}`\n\n"
                                                                     f"**Academy Payout**\n Send `{payout.academy_transaction.amount:5} SLP` from `{self.formatRoninAddress(payout.academy_transaction.from_address)}` to `{self.formatRoninAddress(payout.academy_transaction.to_address)}`\n\n"
                                                                     f"**Admin Fee**\n Send `{payout.fee_transaction.amount:5} SLP` from `{self.formatRoninAddress(payout.fee_transaction.from_address)}` to `{self.formatRoninAddress(payout.fee_transaction.to_address)}`\n\n\n"
                                                                     f"*Message `Yes` if you want to proceed with this transaction.*")

        tz = timezone('EST')
        embed.timestamp = datetime.datetime.now(tz=tz)
        await context.reply(content=context.author.mention, embed=embed)
        confirmation = await self.bot.wait_for('message', timeout=60,
                                               check=lambda message: message.author == context.author)
        hashes = []
        if confirmation.content.lower() == 'yes' and confirmation.channel == context.channel:
            await confirmation.delete()
            embed.description = f"**Executing payout for `{payout.name}`**\n\n\n"
            msg = await context.reply(content=context.author.mention, embed=embed)

            hash = slp_utils.transfer_slp(payout.scholar_transaction, payout.private_key, payout.nonce)
            hashes.append(hash)
            time.sleep(0.250)
            embed.description += f'**SCHOLAR PAYOUT**\n' \
                                 f'Sent `{payout.scholar_transaction.amount} SLP` from `{self.formatRoninAddress(payout.scholar_transaction.from_address)}` to `{self.formatRoninAddress(payout.scholar_transaction.to_address)}`\n' \
                                 f'**Hash: **{hash}\n' \
                                 f'**Explorer: **https://explorer.roninchain.com/tx/{str(hash)}\n\n\n'
            await msg.edit(content=context.author.mention, embed=embed)

            hash = slp_utils.transfer_slp(payout.academy_transaction, payout.private_key, payout.nonce + 1)
            hashes.append(hash)
            time.sleep(0.250)
            embed.description += f'**ACADEMY PAYOUT**\n' \
                                 f'Sent `{payout.academy_transaction.amount}` SLP from `{self.formatRoninAddress(payout.academy_transaction.from_address)}` to `{self.formatRoninAddress(payout.academy_transaction.to_address)}`\n' \
                                 f'**Hash:** {hash}\n' \
                                 f'**Explorer:** https://explorer.roninchain.com/tx/{str(hash)}\n\n\n'
            await msg.edit(content=context.author.mention, embed=embed)

            hash = slp_utils.transfer_slp(payout.fee_transaction, payout.private_key, payout.nonce + 2)
            hashes.append(hash)
            time.sleep(0.250)
            embed.description += f'**ADMIN FEE PAYOUT**\n' \
                                 f'Sent `{payout.fee_transaction.amount}` SLP from `{self.formatRoninAddress(payout.fee_transaction.from_address)}` to `{self.formatRoninAddress(payout.fee_transaction.to_address)}`\n' \
                                 f'**Hash:** {hash}\n' \
                                 f'**Explorer:** https://explorer.roninchain.com/tx/{str(hash)}'
            tz = timezone('EST')
            current_time = datetime.datetime.now(tz=tz)
            embed.timestamp = current_time
            await msg.edit(content=context.author.mention, embed=embed)

            self.write_to_sheets(total_slp=slp_balance, scholar_payout=payout.scholar_transaction.amount,
                                 academy_payout=payout.academy_transaction.amount,
                                 admin_fee=payout.fee_transaction.amount,
                                 hashes=hashes, scholar_name=scholar_name, current_time=current_time)

    def write_to_sheets(self, total_slp, scholar_payout, academy_payout, admin_fee, scholar_name, current_time, hashes):
        current_time = str(current_time.strftime('%d-%m-%y %H:%M:%S'))
        content_lst = [scholar_name, current_time, total_slp, scholar_payout, academy_payout, admin_fee]
        for hash in hashes:
            content_lst.append(f'https://explorer.roninchain.com/tx/{str(hash)}')

        SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
        creds = None
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'cogs/credentials.json', SCOPES)
                creds = flow.run_local_server()
            with open('token.pickle', 'wb') as token:
                pickle.dump(creds, token)

        service = build('sheets', 'v4', credentials=creds)
        sheet = service.spreadsheets().values()
        sheets_id = config["sheets_id"]

        def flatten(t):
            return [item for sublist in t for item in sublist]

        column_days = sheet.get(spreadsheetId=sheets_id, range="A:A").execute()
        if 'values' in column_days:
            column_days = flatten(column_days['values'])
        else:
            column_days = []
        range_row_id = len(column_days) + 1
        for i in [(0, 'A'), (1, 'B'), (2, 'C'), (3, 'D'), (4, 'E'), (5, 'F'), (6, 'G'), (7, 'H'), (8, 'I')]:
            self.write(range=f"{i[1]}{range_row_id}:{i[1]}{range_row_id}",
                       content=content_lst[i[0]], sheet=sheet, sheets_id=sheets_id, append=True)

    @staticmethod
    def write(range, content, sheet, sheets_id, append=False):
        if append:
            body = {
                'values': [[content]]
            }
            sheet.append(spreadsheetId=sheets_id, range=range,
                         valueInputOption='USER_ENTERED', body=body).execute()
        else:
            body = {
                'values': [[content]]
            }
            sheet.update(spreadsheetId=sheets_id, range=range,
                         valueInputOption='USER_ENTERED', body=body).execute()


def setup(bot):
    bot.add_cog(Claim(bot))
