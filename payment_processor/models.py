from dataclasses import dataclass


COLUMNS = [
    "Name",
    "Дата",
    "Тип операции",
    "Тип оплаты",
    "Банк",
    "Контрагент",
    "Номер счета",
    "Объект",
    "Проект",
    "Статья бюджета",
    "Ответственный",
    "Назначение платежа",
    "Ссылка на счет",
    "Сумма",
]


@dataclass
class PaymentRecord:
    name: str
    date: str
    operation_type: str
    payment_type: str
    bank: str
    counterparty: str
    invoice_number: str
    object_name: str
    project: str
    budget_item: str
    responsible: str
    purpose: str
    invoice_link: str
    amount: str

    def as_row(self) -> list[str]:
        return [
            self.name,
            self.date,
            self.operation_type,
            self.payment_type,
            self.bank,
            self.counterparty,
            self.invoice_number,
            self.object_name,
            self.project,
            self.budget_item,
            self.responsible,
            self.purpose,
            self.invoice_link,
            self.amount,
        ]

    @classmethod
    def from_row(cls, row: list[str]) -> "PaymentRecord":
        values = list(row) + [""] * (len(COLUMNS) - len(row))
        return cls(
            name=values[0],
            date=values[1],
            operation_type=values[2],
            payment_type=values[3],
            bank=values[4],
            counterparty=values[5],
            invoice_number=values[6],
            object_name=values[7],
            project=values[8],
            budget_item=values[9],
            responsible=values[10],
            purpose=values[11],
            invoice_link=values[12],
            amount=values[13],
        )
