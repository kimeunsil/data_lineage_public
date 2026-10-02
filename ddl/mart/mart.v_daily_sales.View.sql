CREATE VIEW [mart].[v_daily_sales]
AS
SELECT f.sale_date,
       f.channel,
       p.category_name,
       SUM(f.qty)    AS qty,
       SUM(f.amount) AS amount
FROM dw.fact_sales f
JOIN dw.dim_product p ON p.product_key = f.product_key
GROUP BY f.sale_date, f.channel, p.category_name
GO
