CREATE PROCEDURE dw.usp_load_dim_product
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH latest_price AS (
        SELECT product_id, unit_price,
               ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY order_id DESC) AS rn
        FROM stg.order_items
    )
    INSERT INTO dw.dim_product (product_id, name, category_name, list_price, last_sold_price)
    SELECT p.product_id, p.name, p.category_name, p.list_price, lp.unit_price
    FROM stg.products p
    LEFT JOIN latest_price lp ON lp.product_id = p.product_id AND lp.rn = 1;
END
GO
