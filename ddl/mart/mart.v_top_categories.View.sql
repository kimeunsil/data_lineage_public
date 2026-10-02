CREATE OR ALTER VIEW mart.v_top_categories
AS
WITH monthly AS (
    SELECT DATEFROMPARTS(YEAR(sale_date), MONTH(sale_date), 1) AS month, category_name, SUM(amount) AS amount
    FROM mart.v_daily_sales
    GROUP BY DATEFROMPARTS(YEAR(sale_date), MONTH(sale_date), 1), category_name
)
SELECT month, category_name, amount,
       RANK() OVER (PARTITION BY month ORDER BY amount DESC) AS rnk
FROM monthly
GO
